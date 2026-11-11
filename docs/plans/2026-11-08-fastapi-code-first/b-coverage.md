# 步驟 4b：舊驗證層行為 B1–B68 的去向與釘住的測試

每列對到 `inventory.md` §1 的同一個 B 編號。「測試」欄是 `backend/tests/` 下的 `檔::函式`（helper 寫 `apitest.py::函式`），「處置」欄寫保留、改變（附決定）或刪除（附理由）。`check_b_coverage.py`（同目錄，一次性）檢查 B1–B68 每列都在、每個引用的函式存在、刪除列有理由：

```bash
uv run --no-project --python 3.14 python docs/plans/2026-11-08-fastapi-code-first/check_b_coverage.py
```

| # | 測試 | 處置 |
|---|---|---|
| B1 | test_validation.py::test_health_is_outside_validation | 刪除：全域契約驗證 middleware 不在了；body 上限 middleware 沿用 `/api/` 範圍 |
| B2 | test_validation.py::test_body_over_the_limit | 保留：`app.http.body_limit` |
| B3 | test_validation.py::test_body_over_the_limit_without_content_length | 保留 |
| B4 | test_validation.py::test_body_over_the_limit | 保留；測試現在斷言 detail |
| B5 | test_validation.py::test_body_over_the_limit | 保留：未定義路由也先 413（新斷言）。計畫 D2 的順序句寫 404/405 > 413，與這列矛盾，待 Will 確認（實作維持現狀） |
| B6 | test_validation.py::test_body_read_failure_is_not_a_validation_error | 保留 |
| B7 | test_validation.py::test_valid_request_reaches_the_operation | 保留 |
| B8 | test_validation.py::test_undefined_route | 保留；`redirect_slashes=False`，結尾斜線是 404 不是轉址（新斷言） |
| B9 | test_validation.py::test_undefined_route | 改變：Starlette 的 405 帶 `Allow`，只列第一個比對到路徑的 route 的方法（`DELETE /api/widgets` 回 `Allow: POST`，少了 `GET`）；舊行為不帶 `Allow` |
| B10 | test_validation.py::test_undefined_route | 保留 |
| B11 | test_validation.py::test_secured_operation_without_a_session_is_unauthenticated | 改變（D3）：401 帶 `code: session_invalid` |
| B12 | test_auth_http.py::test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid | 保留（D2）：壞 JSON、錯的 media type、不合 schema 都先 401 |
| B13 | test_auth_http.py::test_only_the_cookie_authenticates, test_auth_http.py::test_an_invalid_cookie_is_cleared | 保留 |
| B14 | test_validation.py::test_unsupported_media_type | 保留：`ApiRoute` |
| B15 | test_validation.py::test_unsupported_media_type | 保留 |
| B16 | test_validation.py::test_a_patch_in_another_media_type_names_merge_patch, test_items_http.py::test_a_patch_is_a_merge_patch | 保留；改變（D2）：PATCH 的 415 帶 `Accept-Patch` |
| B17 | test_validation.py::test_media_type_parameters_are_accepted | 保留；大小寫不同的 type 也接受（新斷言） |
| B18 | test_validation.py::test_merge_patch_body_is_parsed_as_json | 保留 |
| B19 | test_validation.py::test_malformed_json_is_a_bad_request | 保留：`StrictJSONRequest` |
| B20 | test_validation.py::test_malformed_json_is_a_bad_request | 保留；加頂層 `NaN` |
| B21 | test_validation.py::test_malformed_json_is_a_bad_request | 保留 |
| B22 | test_validation.py::test_a_media_type_error_comes_before_a_json_error, test_auth_http.py::test_a_user_who_is_not_an_admin_is_refused_before_the_body_is_checked, test_ratelimit.py::test_a_refused_request_uses_a_token | 改變（D2）：404/405 > 413 > 401/403 > 429 > 415 > 400 > 422 |
| B23 | test_validation.py::test_validation_lists_every_error | 保留：`app.http.errors` |
| B24 | test_validation.py::test_parameter_and_body_errors_are_listed_together | 保留 |
| B25 | test_validation.py::test_validation_codes, test_validation.py::test_a_missing_body_is_required_whatever_its_content_type | 保留：沒有 body 時不看 Content-Type |
| B26 | test_validation.py::test_validation_codes, test_items_http.py::test_create_item_title_rules | 保留 |
| B27 | test_validation.py::test_validation_codes, test_validation.py::test_validation_lists_every_error | 保留：`REQUEST_CONFIG` 的 `extra="forbid"`；值為 null 的多餘欄位也是 `additional_property`（審查 2026-11-09 抓到的回歸，已修） |
| B28 | test_validation.py::test_validation_codes, test_items_http.py::test_a_patch_cannot_null_the_title, test_schemas.py::test_null_is_rejected_where_the_contract_does_not_allow_it | 保留 |
| B29 | test_validation.py::test_validation_codes, test_validation.py::test_every_validation_code_is_a_pydantic_error_type_and_a_contract_code | 保留：`VALIDATION_CODES`；型別不符不做轉換（`strict=True`，新斷言 `"3"` 對整數欄位） |
| B30 | test_validation.py::test_validation_codes | 保留 |
| B31 | test_validation.py::test_validation_codes | 保留 |
| B32 | test_validation.py::test_validation_detail_does_not_echo_the_input | 保留；多驗 enum 的 detail |
| B33 | test_validation.py::test_validation_codes | 保留 |
| B34 | test_validation.py::test_validation_codes, test_items_http.py::test_list_items_refuses_an_invalid_page | 保留 |
| B35 | test_validation.py::test_validation_codes, test_items_http.py::test_list_items_refuses_an_invalid_page | 保留 |
| B36 | test_validation.py::test_validation_codes, test_items_http.py::test_an_item_id_is_a_uuid | 保留：`app.http.schemas.Id` 只收帶連字號的寫法（Pydantic 單獨會接受無連字號、大括號、`urn:uuid:`），不改規格 |
| B37 | test_validation.py::test_a_missing_parameter_is_required | 保留 |
| B38 | test_validation.py::test_validation_codes, test_items_http.py::test_list_items_refuses_an_invalid_page | 保留：`Query(ge=1, le=MAX_LIMIT)` |
| B39 | test_validation.py::test_an_error_type_without_a_code_is_invalid_and_logged | 改變：沒有對照的 Pydantic 錯誤類型回 `invalid` 並記 warning，不再 500 |
| B40 | test_validation.py::test_pattern_dollar_matches_only_the_end, test_validation.py::test_a_matching_pattern_passes_validation, test_openapi.py::test_patterns_mean_the_same_to_the_server_and_its_clients | 保留；ECMA 翻譯器與其單元測試刪除，改由測試擋下規格裡 ECMA 與 pydantic-core 意義不同的 pattern（`\d`、`\w`、`\s`、`\b`、類別外的 `.`） |
| B41 | — | 刪除：執行期不讀契約檔（規格由 route 產生）；Dockerfile 的 `COPY api api` 一起移除 |
| B42 | — | 刪除：執行期沒有 openapi-core |
| B43 | test_problems.py::test_values_outside_the_contract_are_reported, test_problems.py::test_problem_values_are_in_the_contract | 保留：清單改讀產生的規格（`build_openapi()`） |
| B44 | apitest.py::checked_client, test_validation.py::test_secured_operation_without_a_session_is_unauthenticated | 保留（D5）：對 app 自己產生的規格驗證；mutation M6（拿掉自動宣告的 401）由這裡抓到 |
| B45 | apitest.py::_routed | 保留：未定義路由與 health 跳過 |
| B46 | apitest.py::assert_problem | 保留 |
| B47 | — | 刪除：這項檢查的必要條件是「route 與規格各自手寫」，規格改由 route 產生後不成立；前端那側由 `gen-check` 與 `pnpm gen:api:check` 守 |
| B48 | test_routes.py::test_a_route_needs_exactly_one_access_marker, test_routes.py::test_a_route_needs_the_api_route_class, test_routes.py::test_a_request_body_is_a_strict_model, test_routes.py::test_an_include_adds_no_marker_or_rate_limit | 保留；另加 route class、strict body（含巢狀 model）與「include 不得加 marker 或限流」的啟動檢查 |
| B49 | test_openapi.py::test_security_follows_the_access_markers | 保留：security 由 marker 產生 |
| B50 | test_routes.py::test_nothing_is_routed_outside_the_api_but_the_health_checks | 保留 |
| B51 | test_routes.py::test_the_real_app_routes_every_operation | 保留 |
| B52 | test_routes.py::test_every_writable_path_has_a_get | 保留：讀產生的規格 |
| B53 | test_validation.py::test_docs_only_in_dev, test_validation.py::test_docs_serve_the_generated_contract | 4a 已改為 `/api/openapi.json` |
| B54 | test_validation.py::test_docs_only_in_dev | 保留 |
| B55 | test_validation.py::test_docs_only_in_dev | 保留 |
| B56 | test_validation.py::test_docs_only_in_dev | 保留：route check 略過 docs 路徑 |
| B57 | test_problems.py::test_other_http_errors_keep_their_status_and_headers, test_problems.py::test_unknown_path_outside_the_api_is_a_problem | 保留 |
| B58 | test_validation.py::test_a_missing_parameter_is_required, test_validation.py::test_validation_codes | 改寫：`RequestValidationError` handler 是唯一的 422 來源 |
| B59 | test_problems.py::test_unhandled_error_is_logged_not_sent, test_problems.py::test_unhandled_error_log_has_the_stack, test_problems.py::test_failure_after_the_response_started_aborts_it, test_problems.py::test_app_error_after_the_response_started_aborts_it, test_validation.py::test_failure_in_an_operation_is_a_problem | 保留 |
| B60 | test_problems.py::test_app_error_becomes_its_problem, test_problems.py::test_app_error_validation_lists_the_field | 保留 |
| B61 | test_problems.py::test_problem_values_are_in_the_contract | 保留：清單由 `PROBLEM_TYPES`、`PROBLEM_CODES`、`FIELD_ERROR_CODES` 產生，測試比對產生的規格與這三份登錄 |
| B62 | test_problems.py::test_rate_limited_sends_retry_after_rounded_up, test_problems.py::test_rate_limited_without_a_positive_wait_has_no_retry_after | 保留 |
| B63 | test_auth_http.py::test_every_operation_needs_a_session_unless_the_contract_says_public | 改寫：marker 是真正的檢查，`ApiRoute` 在讀 body 之前執行 |
| B64 | test_auth_http.py::test_a_user_who_is_not_an_admin_is_refused_and_it_is_logged | 保留 |
| B65 | test_ratelimit.py::test_a_refused_request_uses_a_token | 改變（D6）：被 415、400、422 擋下的請求也扣額度 |
| B66 | test_auth_http.py::test_a_request_refused_at_the_edge_never_looks_up_its_session | 保留：`BodyLimitMiddleware` 在原本 `ContractValidation` 的位置（最內層） |
| B67 | test_seed.py::test_the_input_follows_create_user_request_and_is_never_echoed, test_seed.py::test_valid_input_passes | 保留 |
| B68 | — | 刪除：`Deps.contract` 與讀檔移除；`tests/testdata/test-openapi.yaml` 換成 `apitest.py` 的 widget router 與 `widget_schemas.py` |
