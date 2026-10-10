#!/usr/bin/env python3
"""Fail-closed source patcher: only v0.6.3, unique transport hook, no data access."""
from pathlib import Path
import hashlib
import sys
import tomllib

MODULE_ANCHOR = '#[path = "request/official_responses_http.rs"]\nmod official_responses_http;'
MODULE_INSERT = '\n#[path = "request/web_search_history.rs"]\npub(crate) mod web_search_history;'
TRANSPORT_ANCHOR = '    let prompt_cache_key = extract_prompt_cache_key(body_for_transport.as_ref());'
TRANSPORT_INSERT = '''    // Independent compact-history fix: final JSON before compression/retries.
    let body_for_transport = crate::gateway::web_search_history::rewrite_http_body(
        request_ctx.request_path,
        &body_for_transport,
        incoming_headers.passthrough_codex_headers(),
        crate::gateway::web_search_history::is_codex_backend(target_url),
    );
'''


def patch(source: Path, helper: Path) -> list[Path]:
    source = source.resolve()
    manifest = tomllib.loads((source / 'Cargo.toml').read_text())
    if manifest.get('workspace', {}).get('package', {}).get('version') != '0.6.3':
        raise ValueError('Refusing non-v0.6.3 source; no files changed')
    root = source / 'crates/service/src/gateway'
    module = root / 'mod.rs'
    transport = root / 'upstream/attempt_flow/transport.rs'
    added = root / 'request/web_search_history.rs'
    if not added.parent.is_dir():
        raise ValueError('Expected upstream request module directory; no files changed')
    if added.exists():
        raise ValueError('Patch already present or file conflict; no files changed')
    # Preserve upstream line endings; do not rewrite a whole file just for CRLF.
    texts = {path: path.read_bytes().decode('utf-8') for path in [module, transport]}
    module_anchor = MODULE_ANCHOR
    if '\r\n' in texts[module]:
        module_anchor = MODULE_ANCHOR.replace('\n', '\r\n')
    for path, anchor in [(module, module_anchor), (transport, TRANSPORT_ANCHOR)]:
        if texts[path].count(anchor) != 1:
            raise ValueError(f'Expected exactly one hook in {path.name}; no files changed')
    # This invariant prevents accidentally attaching the fix after request compression.
    if texts[transport].count('let body_for_request = encode_request_body(') != 1:
        raise ValueError('Compression layout changed; no files changed')
    if texts[transport].index(TRANSPORT_ANCHOR) > texts[transport].index('let body_for_request = encode_request_body('):
        raise ValueError('Unsafe transport hook order; no files changed')
    helper_bytes = helper.read_bytes()
    if b'pub(crate) fn rewrite_http_body(' not in helper_bytes:
        raise ValueError('Wrong helper module; no files changed')
    # All preconditions and reads complete before writing any source files.
    module_insert = MODULE_INSERT.replace('\n', '\r\n') if '\r\n' in texts[module] else MODULE_INSERT
    transport_insert = TRANSPORT_INSERT.replace('\n', '\r\n') if '\r\n' in texts[transport] else TRANSPORT_INSERT
    module.write_bytes(texts[module].replace(module_anchor, module_anchor + module_insert).encode('utf-8'))
    transport.write_bytes(texts[transport].replace(TRANSPORT_ANCHOR, transport_insert + TRANSPORT_ANCHOR).encode('utf-8'))
    added.write_bytes(helper_bytes)
    return [module, transport, added]


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 apply_patch.py SOURCE_DIRECTORY')
    try:
        changed = patch(Path(sys.argv[1]), Path(__file__).with_name('web_search_history.rs'))
    except (ValueError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f'Patch refused: {exc}')
    for path in changed:
        print(f'Patched {path.name}: {hashlib.sha256(path.read_bytes()).hexdigest()}')
