//! Independent, narrowly scoped Codex hosted-search history compatibility fix.
//! No persistent settings, schema changes, history pruning, or response rewriting.

use bytes::Bytes;
use serde_json::{json, Map, Value};

const LITE_HEADER: &str = "x-openai-internal-codex-responses-lite";

pub(crate) fn is_codex_backend(target: &str) -> bool {
    let Ok(url) = reqwest::Url::parse(target) else {
        return false;
    };
    let host = url.host_str().unwrap_or("");
    let path = url.path();
    (host == "chatgpt.com" || host.ends_with(".chatgpt.com"))
        && (path == "/backend-api/codex" || path.starts_with("/backend-api/codex/"))
}

fn truthy(value: &Value) -> bool {
    value.as_bool() == Some(true)
        || value.as_str().is_some_and(|s| s.trim().eq_ignore_ascii_case("true") || s.trim() == "1")
}

fn web_tool(value: &Value) -> bool {
    value.get("type").and_then(Value::as_str).is_some_and(|s| s.starts_with("web_search"))
}

fn is_lite(obj: &Map<String, Value>, headers: &[(String, String)]) -> bool {
    headers.iter().any(|(name, value)| {
        name.eq_ignore_ascii_case(LITE_HEADER) && truthy(&Value::String(value.clone()))
    }) || obj.get("client_metadata").and_then(Value::as_object).is_some_and(|cm| {
        [LITE_HEADER, "ws_request_header_x_openai_internal_codex_responses_lite"]
            .iter().any(|key| cm.get(*key).is_some_and(truthy))
    }) || obj.get("input").and_then(Value::as_array).is_some_and(|input| {
        input.iter().any(|item| item.get("type").and_then(Value::as_str) == Some("additional_tools"))
    })
}

fn ensure_history_tools(path: &str, obj: &mut Map<String, Value>, headers: &[(String, String)]) -> bool {
    if !matches!(path.split('?').next(), Some("/v1/responses" | "/responses")) {
        return false;
    }
    let Some(input) = obj.get("input").and_then(Value::as_array) else {
        return false;
    };
    if !input.iter().any(|item| item.get("type").and_then(Value::as_str) == Some("web_search_call")) {
        return false;
    }
    let top_tools = match obj.get("tools") {
        None | Some(Value::Null) => &[][..],
        Some(Value::Array(tools)) => tools.as_slice(),
        _ => return false,
    };
    if top_tools.iter().any(web_tool) {
        return false;
    }
    let mut original_tool_count = top_tools.len();
    let mut first_additional = None;
    for (index, item) in input.iter().enumerate() {
        if item.get("type").and_then(Value::as_str) != Some("additional_tools") {
            continue;
        }
        if !item.is_object() {
            return false;
        }
        first_additional.get_or_insert(index);
        match item.get("tools") {
            None | Some(Value::Null) => {}
            Some(Value::Array(tools)) => {
                if tools.iter().any(web_tool) {
                    return false;
                }
                original_tool_count += tools.len();
            }
            _ => return false,
        }
    }
    // Invalid or forced policy with no supplied tools must not be silently changed.
    if original_tool_count == 0 {
        match obj.get("tool_choice") {
            None | Some(Value::Null) => {}
            Some(Value::String(choice)) if matches!(choice.as_str(), "auto" | "none") => {}
            _ => return false,
        }
    }
    let lite = is_lite(obj, headers);
    let tool = json!({"type": "web_search", "external_web_access": false});
    if lite {
        let input = obj.get_mut("input").and_then(Value::as_array_mut).expect("validated input");
        if let Some(index) = first_additional {
            let item = input[index].as_object_mut().expect("validated additional_tools");
            let tools = item.entry("tools").or_insert_with(|| json!([]));
            if tools.is_null() {
                *tools = json!([]);
            }
            tools.as_array_mut().expect("validated tools").push(tool);
        } else {
            // Keep remote-v2's trailing trigger last. Do not remove or rewrite history.
            let index = input.iter().position(|item| {
                item.get("type").and_then(Value::as_str) == Some("compaction_trigger")
            }).unwrap_or(input.len());
            input.insert(index, json!({"type": "additional_tools", "role": "developer", "tools": [tool]}));
        }
    } else {
        let tools = obj.entry("tools").or_insert_with(|| json!([]));
        if tools.is_null() {
            *tools = json!([]);
        }
        tools.as_array_mut().expect("validated tools").push(tool);
    }
    if original_tool_count == 0 {
        obj.insert("tool_choice".to_owned(), json!("none"));
    }
    true
}

/// Called on the final uncompressed HTTP payload, before compression and retries.
/// A declared search tool is needed to replay history, not to do a new search.
pub(crate) fn rewrite_http_body(
    path: &str,
    body: &Bytes,
    headers: &[(String, String)],
    codex_backend: bool,
) -> Bytes {
    if !codex_backend || !matches!(path.split('?').next(), Some("/v1/responses" | "/responses")) {
        return body.clone();
    }
    let Ok(mut value) = serde_json::from_slice::<Value>(body.as_ref()) else {
        return body.clone();
    };
    let Some(obj) = value.as_object_mut() else {
        return body.clone();
    };
    let lite = is_lite(obj, headers);
    if !ensure_history_tools(path, obj, headers) {
        return body.clone();
    }
    let Ok(encoded) = serde_json::to_vec(&value) else {
        return body.clone();
    };
    // Never log request content, authorization, account IDs, or encrypted history.
    log::info!("event=codex_web_search_history_hotfix_applied mode={}", if lite { "lite" } else { "standard" });
    Bytes::from(encoded)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        json!({
            "model": "gpt-6.1-sol", "stream": true, "tools": [],
            "input": [
                {"type": "web_search_call", "id": "synthetic-search", "status": "completed"},
                {"type": "reasoning", "encrypted_content": "opaque-synthetic-content"},
                {"type": "message", "role": "user", "content": "Summarize the checkpoint"}
            ]
        })
    }
    fn patch(value: &mut Value, headers: &[(String, String)]) -> bool {
        ensure_history_tools("/v1/responses", value.as_object_mut().unwrap(), headers)
    }

    #[test]
    fn standard_declares_web_and_preserves_every_history_item() {
        let mut value = fixture(); let original_input = value["input"].clone();
        assert!(patch(&mut value, &[]));
        assert_eq!(value["tools"], json!([{"type": "web_search", "external_web_access": false}]));
        assert_eq!(value["tool_choice"], "none"); assert_eq!(value["input"], original_input);
    }
    #[test]
    fn existing_functions_and_policy_are_preserved() {
        let mut value = fixture(); value["tools"] = json!([{"type": "function", "name": "read_file"}]);
        value["tool_choice"] = json!({"type": "function", "name": "read_file"});
        let policy = value["tool_choice"].clone(); assert!(patch(&mut value, &[]));
        assert_eq!(value["tools"][0]["name"], "read_file"); assert_eq!(value["tool_choice"], policy);
    }
    #[test]
    fn missing_and_null_tools_remain_no_execution() {
        for tools in [None, Some(Value::Null), Some(json!([]))] {
            let mut value = fixture(); value.as_object_mut().unwrap().remove("tools");
            if let Some(tools) = tools { value["tools"] = tools; }
            assert!(patch(&mut value, &[])); assert_eq!(value["tool_choice"], "none");
        }
    }
    #[test]
    fn declared_web_variants_and_repeat_calls_are_noops() {
        for kind in ["web_search", "web_search_preview", "web_search_2025_08_26"] {
            let mut value = fixture(); value["tools"] = json!([{"type": kind}]);
            let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
        }
        let mut value = fixture(); assert!(patch(&mut value, &[]));
        let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
    }
    #[test]
    fn additional_tools_existing_web_is_a_noop() {
        let mut value = fixture(); value["input"].as_array_mut().unwrap().push(
            json!({"type": "additional_tools", "role": "developer", "tools": [{"type": "web_search"}]}));
        let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
    }
    #[test]
    fn without_search_history_is_a_noop() {
        let mut value = fixture(); value["input"].as_array_mut().unwrap().remove(0);
        let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
    }
    #[test]
    fn malformed_shapes_and_forced_no_tools_are_not_overridden() {
        for (key, field) in [("tools", json!({})), ("tool_choice", json!("required")),
            ("tool_choice", json!({"type": "function", "name": "missing"}))] {
            let mut value = fixture(); value[key] = field;
            let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
        }
        let mut value = fixture(); value["input"].as_array_mut().unwrap().push(
            json!({"type": "additional_tools", "tools": "invalid"}));
        let before = value.clone(); assert!(!patch(&mut value, &[])); assert_eq!(value, before);
    }
    #[test]
    fn lite_header_places_declaration_before_last_trigger() {
        let mut value = fixture(); value["input"].as_array_mut().unwrap().push(json!({"type": "compaction_trigger"}));
        assert!(patch(&mut value, &[(LITE_HEADER.to_uppercase(), "true".to_owned())]));
        assert_eq!(value["tools"], json!([])); assert_eq!(value["tool_choice"], "none");
        let input = value["input"].as_array().unwrap(); assert_eq!(input.last().unwrap()["type"], "compaction_trigger");
        assert_eq!(input[input.len()-2]["type"], "additional_tools");
    }
    #[test]
    fn lite_metadata_and_existing_additional_tools_are_supported() {
        for key in [LITE_HEADER, "ws_request_header_x_openai_internal_codex_responses_lite"] {
            let mut value = fixture(); value["client_metadata"] = json!({});
            value["client_metadata"][key] = json!("true");
            assert!(patch(&mut value, &[])); assert_eq!(value["tools"], json!([]));
            assert_eq!(value["input"].as_array().unwrap().last().unwrap()["type"], "additional_tools");
        }
        let mut value = fixture(); value["tool_choice"] = json!("auto");
        value["input"].as_array_mut().unwrap().push(json!({"type": "additional_tools", "role": "developer", "tools": [{"type": "function", "name": "read_file"}]}));
        assert!(patch(&mut value, &[])); assert_eq!(value["tool_choice"], "auto");
        assert_eq!(value["input"].as_array().unwrap().last().unwrap()["tools"].as_array().unwrap().len(), 2);
    }
    #[test]
    fn standalone_compact_and_chat_completions_are_byte_preserved() {
        let body = Bytes::from(serde_json::to_vec_pretty(&fixture()).unwrap());
        for path in ["/v1/responses/compact", "/responses/compact", "/v1/chat/completions", "/rpc"] {
            assert_eq!(rewrite_http_body(path, &body, &[], true), body);
        }
    }
    #[test]
    fn api_key_and_external_targets_are_byte_preserved() {
        let body = Bytes::from(serde_json::to_vec_pretty(&fixture()).unwrap());
        assert_eq!(rewrite_http_body("/v1/responses", &body, &[], false), body);
        for target in ["https://api.openai.com/v1/responses", "https://evilchatgpt.com/backend-api/codex/responses",
            "https://chatgpt.com.evil.invalid/backend-api/codex/responses", "https://chatgpt.com/other", "not-a-url"] {
            assert!(!is_codex_backend(target));
        }
        assert!(is_codex_backend("https://chatgpt.com/backend-api/codex/responses"));
    }
    #[test]
    fn json_errors_and_string_input_are_byte_preserved() {
        for raw in ["{invalid", "null", "[]", "{\"input\":\"hello\"}"] {
            let body = Bytes::from(raw.to_owned()); assert_eq!(rewrite_http_body("/v1/responses", &body, &[], true), body);
        }
    }
    #[test]
    fn http_streaming_and_non_streaming_preserve_the_transport_field() {
        for stream in [false, true] {
            let mut value = fixture(); value["stream"] = json!(stream);
            let body = Bytes::from(serde_json::to_vec(&value).unwrap());
            let result = rewrite_http_body("/v1/responses?query=kept", &body, &[], true);
            let output: Value = serde_json::from_slice(result.as_ref()).unwrap();
            assert_eq!(output["stream"], stream); assert_eq!(output["input"], value["input"]);
        }
    }
}
