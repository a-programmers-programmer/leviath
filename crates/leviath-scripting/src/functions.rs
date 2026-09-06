//! Leviath functions exposed to Rhai scripts.

use rhai::{Dynamic, Engine, EvalAltResult, Map, Position};

/// Register Leviath functions in the Rhai engine.
pub fn register_functions(engine: &mut Engine) {
    // String operations
    engine.register_fn("contains", |text: &str, pattern: &str| -> bool {
        text.contains(pattern)
    });

    engine.register_fn("starts_with", |text: &str, pattern: &str| -> bool {
        text.starts_with(pattern)
    });

    engine.register_fn("ends_with", |text: &str, pattern: &str| -> bool {
        text.ends_with(pattern)
    });

    engine.register_fn("trim", |text: &str| -> String { text.trim().to_string() });

    engine.register_fn("join", |arr: rhai::Array, separator: &str| -> String {
        arr.iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(separator)
    });

    engine.register_fn("split", |text: &str, separator: &str| -> rhai::Array {
        text.split(separator)
            .map(|s| rhai::Dynamic::from(s.to_string()))
            .collect()
    });

    // Token counting (approximate)
    engine.register_fn("count_tokens", |text: &str| -> i64 {
        leviath_core::estimate_tokens(text) as i64
    });

    // Content validation
    engine.register_fn("is_json", |text: &str| -> bool {
        serde_json::from_str::<serde_json::Value>(text).is_ok()
    });

    engine.register_fn("is_mermaid", |text: &str| -> bool {
        text.contains("graph")
            || text.contains("sequenceDiagram")
            || text.contains("classDiagram")
            || text.contains("stateDiagram")
            || text.contains("erDiagram")
            || text.contains("flowchart")
    });

    engine.register_fn("is_markdown", |text: &str| -> bool {
        // Very permissive - just check for common markdown markers
        text.contains("##") || text.contains("**") || text.contains("```") || !text.is_empty()
    });

    engine.register_fn("is_empty", |text: &str| -> bool { text.trim().is_empty() });

    // Pure JSON helpers are shared by every sandboxed script engine, including
    // stage and region hooks. Keeping these here prevents the hook engine from
    // accidentally receiving the I/O host functions that script tools use.
    engine.register_fn("parse_json", |s: &str| -> JsonResult<Dynamic> {
        parse_json(s)
    });
    engine.register_fn("to_json", |v: Dynamic| -> JsonResult<String> {
        to_json(&v)
    });
    // Rhai's map package can register a more-specific `to_json(&mut Map)`;
    // register the same strict serializer for maps so object values do not
    // fall through to Rhai's debug formatter (`\\u{...}` is not JSON).
    engine.register_fn("to_json", |map: Map| -> JsonResult<String> {
        let value = Dynamic::from_map(map);
        to_json(&value)
    });
}

type JsonResult<T> = std::result::Result<T, Box<EvalAltResult>>;

/// Parse a JSON string into the plain Rhai data representation.
pub(crate) fn parse_json(s: &str) -> JsonResult<Dynamic> {
    let value: serde_json::Value = serde_json::from_str(s).map_err(|e| {
        Box::new(EvalAltResult::ErrorRuntime(
            format!("parse_json: {e}").into(),
            Position::NONE,
        ))
    })?;
    rhai::serde::to_dynamic(value)
}

/// Serialize a plain Rhai value with serde_json's strict escaping and shape
/// rules. Values without a JSON representation remain script errors.
pub(crate) fn to_json(value: &Dynamic) -> JsonResult<String> {
    let json: serde_json::Value = rhai::serde::from_dynamic(value)?;
    Ok(json.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rhai::Engine;

    fn engine() -> Engine {
        let mut e = Engine::new();
        register_functions(&mut e);
        e
    }

    // --- contains ---

    #[test]
    fn contains_returns_true_when_pattern_present() {
        let e = engine();
        let result: bool = e.eval(r#"contains("hello world", "world")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn contains_returns_false_when_pattern_absent() {
        let e = engine();
        let result: bool = e.eval(r#"contains("hello", "xyz")"#).unwrap();
        assert!(!result);
    }

    #[test]
    fn contains_empty_pattern_always_matches() {
        let e = engine();
        let result: bool = e.eval(r#"contains("hello", "")"#).unwrap();
        assert!(result);
    }

    // --- starts_with ---

    #[test]
    fn starts_with_true() {
        let e = engine();
        let result: bool = e.eval(r#"starts_with("hello", "he")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn starts_with_false() {
        let e = engine();
        let result: bool = e.eval(r#"starts_with("hello", "lo")"#).unwrap();
        assert!(!result);
    }

    // --- ends_with ---

    #[test]
    fn ends_with_true() {
        let e = engine();
        let result: bool = e.eval(r#"ends_with("hello", "lo")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn ends_with_false() {
        let e = engine();
        let result: bool = e.eval(r#"ends_with("hello", "he")"#).unwrap();
        assert!(!result);
    }

    // --- trim ---

    #[test]
    fn trim_removes_whitespace() {
        let e = engine();
        let result: String = e.eval(r#"trim("  hi  ")"#).unwrap();
        assert_eq!(result, "hi");
    }

    #[test]
    fn trim_no_op_on_clean_string() {
        let e = engine();
        let result: String = e.eval(r#"trim("hi")"#).unwrap();
        assert_eq!(result, "hi");
    }

    // --- join ---

    #[test]
    fn join_with_comma() {
        let e = engine();
        let result: String = e.eval(r#"join(["a", "b", "c"], ",")"#).unwrap();
        assert_eq!(result, "a,b,c");
    }

    #[test]
    fn join_empty_array() {
        let e = engine();
        let result: String = e.eval(r#"join([], ",")"#).unwrap();
        assert_eq!(result, "");
    }

    #[test]
    fn join_single_element() {
        let e = engine();
        let result: String = e.eval(r#"join(["only"], "-")"#).unwrap();
        assert_eq!(result, "only");
    }

    // --- split ---

    #[test]
    fn split_by_comma() {
        let e = engine();
        let result: rhai::Array = e.eval(r#"split("a,b,c", ",")"#).unwrap();
        assert_eq!(result.len(), 3);
        assert_eq!(result[0].clone_cast::<String>(), "a");
        assert_eq!(result[1].clone_cast::<String>(), "b");
        assert_eq!(result[2].clone_cast::<String>(), "c");
    }

    #[test]
    fn split_no_separator_found() {
        let e = engine();
        let result: rhai::Array = e.eval(r#"split("abc", ",")"#).unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].clone_cast::<String>(), "abc");
    }

    // --- count_tokens ---

    #[test]
    fn count_tokens_approximate() {
        let e = engine();
        let result: i64 = e.eval(r#"count_tokens("hello world")"#).unwrap();
        // "hello world" is 11 chars, ceil(11/4) = 3
        assert_eq!(result, 3);
    }

    #[test]
    fn count_tokens_empty() {
        let e = engine();
        let result: i64 = e.eval(r#"count_tokens("")"#).unwrap();
        assert_eq!(result, 0);
    }

    // --- is_json ---

    #[test]
    fn is_json_valid_object() {
        let e = engine();
        let result: bool = e.eval(r#"is_json("{}")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_json_valid_array() {
        let e = engine();
        let result: bool = e.eval(r#"is_json("[1,2,3]")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_json_invalid() {
        let e = engine();
        let result: bool = e.eval(r#"is_json("not json")"#).unwrap();
        assert!(!result);
    }

    // --- is_mermaid ---

    #[test]
    fn is_mermaid_with_graph_keyword() {
        let e = engine();
        let result: bool = e.eval(r#"is_mermaid("graph TD; A-->B")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_mermaid_with_sequence_diagram() {
        let e = engine();
        let result: bool = e
            .eval(r#"is_mermaid("sequenceDiagram\nA->>B: Hi")"#)
            .unwrap();
        assert!(result);
    }

    #[test]
    fn is_mermaid_with_flowchart() {
        let e = engine();
        let result: bool = e.eval(r#"is_mermaid("flowchart LR")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_mermaid_false_for_plain_text() {
        let e = engine();
        let result: bool = e.eval(r#"is_mermaid("just some text")"#).unwrap();
        assert!(!result);
    }

    // --- is_markdown ---

    #[test]
    fn is_markdown_with_heading() {
        let e = engine();
        let script = "is_markdown(\"## Heading\")";
        let result: bool = e.eval(script).unwrap();
        assert!(result);
    }

    #[test]
    fn is_markdown_with_bold() {
        let e = engine();
        let result: bool = e.eval(r#"is_markdown("some **bold** text")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_markdown_with_code_fence() {
        let e = engine();
        let result: bool = e.eval(r#"is_markdown("```code```")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_markdown_plain_nonempty_text_returns_true_via_nonempty_fallback() {
        let e = engine();
        // Text with none of the markdown markers - falls through to !text.is_empty()
        let result: bool = e.eval(r#"is_markdown("just plain text")"#).unwrap();
        assert!(result);
    }

    // --- is_empty ---

    #[test]
    fn is_empty_true_for_empty() {
        let e = engine();
        let result: bool = e.eval(r#"is_empty("")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_empty_true_for_whitespace_only() {
        let e = engine();
        let result: bool = e.eval(r#"is_empty("   ")"#).unwrap();
        assert!(result);
    }

    #[test]
    fn is_empty_false_for_content() {
        let e = engine();
        let result: bool = e.eval(r#"is_empty("hi")"#).unwrap();
        assert!(!result);
    }

    #[test]
    fn json_helpers_round_trip_arrays_maps_and_unicode_strictly() {
        let e = engine();
        let result: String = e
            .eval(r##"to_json(parse_json("{\"items\":[1,true,null],\"text\":\"é\\n\"}"))"##)
            .unwrap();
        assert_eq!(
            result,
            r#"{"items":[1,true,null],"text":"é\n"}"#
        );
        let parsed: serde_json::Value = serde_json::from_str(&result).expect("strict JSON");
        assert_eq!(parsed["items"][1], true);
        assert_eq!(parsed["text"], "é\n");
    }
}
