# PENRS Fallback Inventory

This document lists fallback/default behavior currently present in the codebase and when each should be triggered.

## `utils.py`

| Location | Fallback behavior | Should trigger when |
|---|---|---|
| `PENRS_CACHE_DIR = os.getenv(..., ".penrs_cache")` | Uses `.penrs_cache` directory if env var is unset. | `PENRS_CACHE_DIR` is not defined in environment. |
| `cache_get()` missing file | Returns `None` (cache miss). | Cache file does not exist for computed key. |
| `cache_get()` read/parse failure | Returns `None` (cache miss). | Cache file exists but cannot be read or parsed as JSON. |
| `cache_get()` missing `_cached_at` | Returns `None` (cache miss). | Cache record lacks timestamp metadata. |
| `cache_get()` invalid `_cached_at` | Returns `None` (cache miss). | Timestamp is malformed or not ISO-compatible. |
| `cache_get()` expired cache | Returns `None` (cache miss). | Cache age is `>= max_age_hours`. |
| `cache_get()` backward compatibility branch | If `payload` key is missing, returns non-metadata keys as inferred payload. | Reading legacy cache records created before `payload` wrapper existed. |
| `_api_request()` arg normalization | Converts `params`/`headers` `None` to `{}`. | Callers pass `None` for params/headers. |
| `_api_request()` timeout handling | Returns structured error dict (`Request timed out`). | HTTP request exceeds configured timeout. |
| `_api_request()` request error handling | Returns structured error dict (`Request failed`). | Transport/DNS/connection-level request failure occurs. |
| `_api_request()` retry loop | Retries on `429`/`503` with exponential backoff. | Remote API is rate-limiting or temporarily unavailable. |
| `_api_request()` non-JSON response | Returns `{"text": response.text}` instead of failing JSON parse. | Response is successful but body is not valid JSON. |
| `_DEFAULT_RPM_LIMIT = int(os.getenv(..., "60"))` | Uses `60` RPM default for generic APIs. | `PENRS_DEFAULT_RPM_LIMIT` is unset. |
| `_limits_for_api()` default path | Uses caller-provided `rpm_limit` or `_DEFAULT_RPM_LIMIT` for unknown API names. | API name is not recognized as Alpha Vantage or SEC/Edgar. |
| `_normalize_date_range(None)` | Normalizes to `(None, None)`. | No date range was supplied. |
| `fetchers = fetchers or {}` in `penrs_fetch_document()` | Uses empty fetcher map when omitted. | Caller does not pass fetcher mapping. |
| Missing fetcher in `penrs_fetch_document()` | Uses `_missing_fetcher` error result per API. | Document route points to an API with no registered fetcher function. |
| `asyncio.gather(..., return_exceptions=True)` in `penrs_fetch_document()` | Isolates per-API failures; continues aggregating other APIs. | Any routed API raises exception while others may still succeed. |
| `penrs_fetch_document()` final status fallback | Returns `{"status": "not_released"}` with attempted APIs/errors when no usable source data exists. | All API attempts fail, error, or return unusable/empty data. |

## `penrs_mcp_server.py`

| Location | Fallback behavior | Should trigger when |
|---|---|---|
| `ALPHA_VANTAGE_API_KEY = os.getenv(..., "demo")` | Uses Alpha Vantage `demo` key. | `ALPHA_VANTAGE_API_KEY` is unset. |
| `SEC_USER_AGENT = os.getenv(..., "PENRS/1.0")` | Uses default SEC user-agent. | `SEC_USER_AGENT` is unset. |
| `PENRS_LOG_DIR = os.getenv(..., ".penrs_logs")` | Uses `.penrs_logs` directory. | `PENRS_LOG_DIR` is unset. |
| Conditional OpenFDA/NCBI API key injection | Omits `api_key` parameter if key is absent. | `OPENFDA_API_KEY` or `NCBI_API_KEY` is unset. |

## `worker_nodes.ipynb` (runtime module for workers)

| Location | Fallback behavior | Should trigger when |
|---|---|---|
| `_callable_accepts_kwarg()` exception path | Returns `False` if signature inspection fails. | Callable has no inspectable signature or inspect raises. |
| `_load_rubric_from_json()` missing file | Returns error rubric payload. | `rubrics.json` does not exist. |
| `_load_rubric_from_json()` read/parse failure | Returns error rubric payload. | `rubrics.json` cannot be read or parsed. |
| `_load_rubric_from_json()` missing rubric id | Returns error rubric payload. | Rubric file exists but requested `rubric_id` key is absent/not dict. |
| `_load_rubric_from_json()` wrong top-level shape | Returns error rubric payload. | `rubrics.json` top-level is not a JSON object. |
| `truncate_for_context()` marker-too-long branch | Hard truncates head to `max_chars`. | Truncation marker itself is too long to fit in budget. |
| `PENRSWorker.__init__` default `rubric_fetcher` | Uses `_load_rubric_from_json`. | Caller does not provide `rubric_fetcher`. |
| `PENRSWorker.__init__` default `document_fetcher` | Uses `penrs_fetch_document`. | Caller does not provide `document_fetcher`. |
| `PENRSWorker.__init__` default `llm_invoker` | Uses schema-safe lambda returning `{}`. | Caller does not provide `llm_invoker`. |
| `parse_json_response()` default payload | Returns `{"score": 0.0, "thesis": "Parse failure", "evidence_nodes": []}` on invalid input. | Response is `None`, empty, non-JSON, malformed, or schema-incomplete. |
| `parse_json_response()` score coercion fallback | Uses `0.0` if score cannot be coerced. | Payload has non-numeric `score`. |
| `parse_json_response()` thesis type fallback | Uses default thesis text if thesis is not string. | Payload has non-string `thesis`. |
| `parse_json_response()` evidence type fallback | Uses empty list if evidence_nodes is not list. | Payload has wrong type for `evidence_nodes`. |
| `run()` rubric wrapping | Converts non-dict rubric to `{"rubric": rubric_raw}`. | Rubric fetcher returns primitive/list/etc. |
| `run()` document result fallback | Converts non-dict document response to `{"status": "not_released", "data": {}}`. | Document fetcher returns non-dict payload. |
| `run()` not released path | Returns worker `not_released` payload with `apis_attempted` fallback to `[]`. | Document fetcher status is `not_released`. |
| `run()` LLM call-mode fallback | Calls invoker without `system` kwarg if invoker does not accept it. | LLM callable signature does not include `system`/`**kwargs`. |
| Evidence validation fallback | Drops evidence nodes whose `verbatim_quote` is missing/non-string/not present in excerpt. | LLM returns invalid or hallucinated quote nodes. |
| Score neutralization safety fallback | Forces `score = 0.0` and appends system note. | All provided evidence nodes are hallucinated, or high-conviction score (`|score| >= 0.5`) has zero evidence nodes. |

## `orchestrator.ipynb` (runtime module for orchestration)

| Location | Fallback behavior | Should trigger when |
|---|---|---|
| `_extract_star_rating()` fallback | Derives star rating from `signal_density` if missing. | Worker result does not provide `result.star_rating`. |
| `_coerce_float_or_zero()` | Returns `0.0` on invalid numeric conversion. | Worker identity fields (`weight`, `signal_density`) are missing or non-numeric. |
| `_worker_identity()` name fallback | Uses worker class name if `.name` is missing. | Worker object has no `name` attribute. |
| `run_all_workers()` empty workers | Returns `[]`. | Caller provides no workers. |
| `run_all_workers()` exception isolation | Wraps worker exception into `{"status": "error", ...}` and continues. | Any worker raises during `run()`. |
| `run_all_workers()` type fallback | Wraps non-dict worker return into error payload. | Worker returns non-dict result. |
| `_evaluate_with_arbiter()` no valid inputs | Returns `{"status": "not_available", "weighted_score": 0.0, ...}`. | No worker result is both `status=available` and schema-usable. |
| `_evaluate_with_arbiter()` exception handling | Returns `{"status": "error", "weighted_score": 0.0, ...}`. | Arbiter raises validation/runtime exception. |
| `_safe_filename_component()` | Uses `"unknown"` if sanitized component becomes empty. | Ticker/date fields sanitize to empty string. |
| `MasterAgent.synthesize()` weighted score default | Uses `0.0` if arbiter weighted score is missing/non-numeric. | `arbiter_result.weighted_score` absent or invalid type. |
| `MasterAgent.synthesize()` worker/doc metadata fallback | Uses `unknown_worker` and `unknown_doc_type` for cache key derivation. | Worker metadata or document type is missing/empty/invalid. |
| `run_penrs()` worker list default | Uses empty list for workers. | `workers` arg is `None`. |
| `run_penrs()` default agent creation | Instantiates `ArbiterAgent()`/`MasterAgent()` automatically. | `arbiter` and/or `master` args are `None`. |
| `run_penrs()` master result fallback | Replaces non-dict master output with error dict. | Master returns non-dict value. |
| `run_penrs()` generated time fallback | Uses current UTC time. | `now` arg is `None`. |
| `run_penrs()` top-level evidence fallback | Uses `master_result.get("evidence", [])`. | Master output omits `evidence`. |

## `penrs_tui.py`

| Location | Fallback behavior | Should trigger when |
|---|---|---|
| `EvidenceListItem.__init__` cache-key source fallback | Reads `cache_key` from `kwargs` when explicit `cache_key_value` is omitted. | Caller passes `cache_key` kwarg instead of positional/keyword `cache_key_value`. |
| `_update_dialogue()` guard paths | No-op if query method missing, selector absent, or update fails. | Running in contexts/tests without full widget tree. |
| `_load_report_from_path()` parse error branch | Updates UI with parse-error placeholders and clears evidence panes. | Selected JSON report is unreadable/invalid. |
| `_load_report_from_path()` evidence source fallback | Uses `master.evidence` if top-level `evidence` is not a list. | Report payload lacks valid top-level evidence list. |
| `on_evidence_selected()` missing cache file | Logs `[missing cache file]` in ground-truth pane. | Referenced cache key file does not exist. |
| `on_evidence_selected()` cache parse error | Logs `[cache parse error]`. | Cache file exists but cannot be read/parsed. |
| `on_evidence_selected()` payload fallback | Uses whole cache record if `payload` key missing. | Cache record is legacy/non-standard shape. |
| `_extract_raw_text()` text-key fallback chain | Prefers `text` -> `raw_text` -> `content` -> `document` -> `body`; else JSON/string coercion. | Payload has varying schema across data sources. |
| `AuditScreen._load_report_from_path()` summary widget fallback | Returns silently if summary widget is unavailable. | Auditor context without `#report_summary`. |
| `AuditScreen._load_report_from_path()` summary parse fallback | Writes `[summary unavailable: report parse error]`. | Report JSON cannot be parsed. |
| `AuditScreen._load_report_from_path()` summary text fallback | Generates summary via `generate_report_summary()` if `.txt` missing/unreadable/empty. | Sidecar summary file absent, unreadable, or blank. |
| `normalize_historical_date()` | Defaults to current UTC date string. | User leaves date input blank. |
| `_extract_quote()` neutral fallback | Returns neutral score and no evidence. | No positive/negative marker phrase is found in worker text. |
| `_fetch_worker_payloads()` default Alpha key | Uses `demo` API key for Alpha Vantage. | `ALPHA_VANTAGE_API_KEY` is unset. |
| `_fetch_worker_payloads()` optional API key params | Omits OpenFDA/NCBI `api_key` param if env vars are absent. | `OPENFDA_API_KEY`/`NCBI_API_KEY` unset. |
| `run_frontier_analysis()` weighted score default | Uses `0.0` if `total_weight` is zero. | No worker payloads were collected successfully. |

## Notes

- This list reflects current fallback/default behavior in code, not necessarily ideal behavior.
- If you want, I can generate a second file ranking these by risk (silent fallback vs. explicit error) and recommending which ones to remove or harden first.
