//! What a region will accept: content-format schemas and their validators.
//!
//! Split out of `region.rs` because it is a separate question from how a region
//! holds and evicts entries - this decides whether a write is well-formed at
//! all, before any of that applies.

use serde::{Deserialize, Serialize};

/// Enforces that content matches expected format (e.g., mermaid diagrams only,
/// JSON only, code only). Schemas can include multiple validators that are
/// checked when content is added to a region.
///
/// When [`Self::content_schema`] is set (draft JSON Schema), writes must both
/// parse as the declared [`ContentFormat`] and validate against that schema.
/// A pending [`Self::schema_path`] is resolved against the blueprint directory
/// before spawn (see [`crate::Blueprint::resolve_region_content_schemas`]).
#[derive(Debug, Serialize, Deserialize)]
pub struct RegionSchema {
    /// Expected content format
    pub format: ContentFormat,

    /// Optional custom validation script (Rhai)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub custom_script: Option<String>,

    /// Optional draft JSON Schema applied to the parsed JSON value when
    /// [`ContentFormat::Json`] is in force (or whenever content is JSON text).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content_schema: Option<serde_json::Value>,

    /// Relative path to a `.json` schema file beside the blueprint. Loaded into
    /// [`Self::content_schema`] by
    /// [`crate::Blueprint::resolve_region_content_schemas`]. Cleared once loaded.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub schema_path: Option<String>,
}

impl Clone for RegionSchema {
    fn clone(&self) -> Self {
        Self {
            format: self.format.clone(),
            custom_script: self.custom_script.clone(),
            content_schema: self.content_schema.clone(),
            schema_path: self.schema_path.clone(),
        }
    }
}

impl RegionSchema {
    /// Create a new schema with the specified format.
    pub fn new(format: ContentFormat) -> Self {
        Self {
            format,
            custom_script: None,
            content_schema: None,
            schema_path: None,
        }
    }

    /// Attach a draft JSON Schema that entries must satisfy.
    pub fn with_content_schema(mut self, schema: serde_json::Value) -> Self {
        self.content_schema = Some(schema);
        self
    }

    /// Remember a schema file path to load later against the blueprint dir.
    pub fn with_schema_path(mut self, path: impl Into<String>) -> Self {
        self.schema_path = Some(path.into());
        self
    }

    /// Add a custom validation script.
    pub fn with_custom_script(mut self, script: String) -> Self {
        self.custom_script = Some(script);
        self
    }

    /// Load [`Self::schema_path`] from `base_dir` into [`Self::content_schema`].
    ///
    /// No-op when no path is pending. The path must resolve inside `base_dir`.
    pub fn resolve_schema_path(&mut self, base_dir: &std::path::Path) -> crate::error::Result<()> {
        let Some(rel) = self.schema_path.take() else {
            return Ok(());
        };
        let path = base_dir.join(&rel);
        if !crate::resolves_within(&path, base_dir) {
            return Err(crate::error::Error::ValidationFailed(format!(
                "schema path '{rel}' escapes the blueprint directory"
            )));
        }
        let text = std::fs::read_to_string(&path).map_err(|e| {
            crate::error::Error::ValidationFailed(format!(
                "cannot read region schema '{}': {e}",
                path.display()
            ))
        })?;
        let value: serde_json::Value = serde_json::from_str(&text).map_err(|e| {
            crate::error::Error::ValidationFailed(format!(
                "region schema '{}' is not valid JSON: {e}",
                path.display()
            ))
        })?;
        // Refuse a schema that will not compile: better a load error than a
        // region that silently accepts everything because jsonschema skipped it.
        if let Err(e) = jsonschema::validator_for(&value) {
            return Err(crate::error::Error::ValidationFailed(format!(
                "region schema '{}' does not compile: {e}",
                path.display()
            )));
        }
        self.content_schema = Some(value);
        Ok(())
    }

    /// Validate content against this schema.
    pub fn validate(&self, content: &str) -> crate::error::Result<()> {
        match &self.format {
            ContentFormat::Json => {
                let value: serde_json::Value =
                    serde_json::from_str(content).map_err(|e| {
                        crate::error::Error::ValidationFailed(format!("Invalid JSON: {}", e))
                    })?;
                self.validate_json_content(&value)?;
            }
            ContentFormat::Mermaid => {
                // Basic mermaid syntax validation
                if !content.contains("graph")
                    && !content.contains("sequenceDiagram")
                    && !content.contains("classDiagram")
                    && !content.contains("stateDiagram")
                    && !content.contains("erDiagram")
                    && !content.contains("journey")
                    && !content.contains("gantt")
                    && !content.contains("pie")
                    && !content.contains("flowchart")
                {
                    return Err(crate::error::Error::ValidationFailed(
                        "Mermaid diagrams must contain a valid diagram type (graph, sequenceDiagram, etc.)".to_string()
                    ));
                }
            }
            ContentFormat::Code { .. } => {
                // Basic code validation - just check it's not empty
                if content.trim().is_empty() {
                    return Err(crate::error::Error::ValidationFailed(
                        "Code cannot be empty".to_string(),
                    ));
                }
            }
            ContentFormat::Markdown => {
                // Markdown is very permissive, just check it's not empty
                if content.trim().is_empty() {
                    return Err(crate::error::Error::ValidationFailed(
                        "Markdown content cannot be empty".to_string(),
                    ));
                }
            }
            ContentFormat::Text | ContentFormat::Custom { .. } => {
                // Text has no restrictions, Custom is handled by scripting layer.
                // A content_schema on Text still means the body must be JSON that
                // satisfies it (Adapter hashmap plans are JSON text).
                if self.content_schema.is_some() {
                    let value: serde_json::Value =
                        serde_json::from_str(content).map_err(|e| {
                            crate::error::Error::ValidationFailed(format!("Invalid JSON: {}", e))
                        })?;
                    self.validate_json_content(&value)?;
                }
            }
        }

        Ok(())
    }

    fn validate_json_content(&self, value: &serde_json::Value) -> crate::error::Result<()> {
        let Some(schema) = &self.content_schema else {
            return Ok(());
        };
        let validator = jsonschema::validator_for(schema).map_err(|e| {
            crate::error::Error::ValidationFailed(format!(
                "region content schema does not compile: {e}"
            ))
        })?;
        // Collect a short refusal the model can act on; three lines is enough.
        let errors: Vec<String> = validator
            .iter_errors(value)
            .take(3)
            .map(|e| format!("{}: {e}", e.instance_path()))
            .collect();
        if errors.is_empty() {
            return Ok(());
        }
        Err(crate::error::Error::ValidationFailed(format!(
            "content failed JSON Schema: {}",
            errors.join("; ")
        )))
    }
}

/// Content format types that can be enforced via schemas.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum ContentFormat {
    /// Plain text, no formatting requirements
    Text,

    /// Valid JSON
    Json,

    /// Mermaid diagram syntax
    Mermaid,

    /// Source code in a specific language
    Code {
        /// The language label, used for the fence and nothing else - no
        /// per-language parsing happens.
        language: String,
    },

    /// Markdown formatted text
    Markdown,

    /// Custom format with user-defined validation
    Custom {
        /// The author's own name for the format, matched against the validator
        /// registered for it.
        format_name: String,
    },
}

impl ContentFormat {
    /// Parse a blueprint `format = "..."` string.
    pub fn from_blueprint_str(s: &str) -> Result<Self, String> {
        let s = s.trim();
        Ok(match s {
            "json" | "JSON" => ContentFormat::Json,
            "mermaid" | "Mermaid" => ContentFormat::Mermaid,
            "markdown" | "md" => ContentFormat::Markdown,
            "text" | "plain" => ContentFormat::Text,
            other if other.starts_with("code:") || other.starts_with("code/") => {
                let language = other
                    .split_once([':', '/'])
                    .map(|(_, lang)| lang.trim().to_string())
                    .filter(|l| !l.is_empty())
                    .unwrap_or_else(|| "text".to_string());
                ContentFormat::Code { language }
            }
            "code" => ContentFormat::Code {
                language: "text".to_string(),
            },
            other => ContentFormat::Custom {
                format_name: other.to_string(),
            },
        })
    }
}

/// Trait for content validators.
///
/// Validators check whether content meets specific requirements before
/// it's added to a region. This enables enforcing architectural constraints
/// like "only mermaid diagrams in the architecture region".
pub trait Validator: Send + Sync {
    /// Validate content and return an error message if invalid.
    fn validate(&self, content: &str) -> std::result::Result<(), crate::error::ValidationError>;

    /// Get a description of what this validator checks.
    fn description(&self) -> &str;
}
