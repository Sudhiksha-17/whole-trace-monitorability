# SHADE-Arena tool inventory and action mapping

## Data and counts

Source: `AlignmentResearch/telling-fibs-eval-transcripts`, revision `97d20337785f68062befa9324d97d7c81063b0c2`. The scan used only the three `shade_public-*` shards and filtered to `arm == "honest"` before converting transcript columns to Python objects. No BashArena or attack calls were inspected.

Calls came from assistant `tool_calls` in `messages_json`; declarations came from `tools_json`, and step indices from `steps`. Counts include every call position throughout each episode, including failures, helpers and lifecycle calls. They count submitted calls, not successful world changes. No recorded tools were executed.

| Config suffix | Honest episodes | Declared names, union over episodes | Called names | Calls | Assistant turns | Missing step records |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen | 100 | 112 | 72 | 7,092 | 3,611 | 0 |
| Kimi | 100 | 112 | 86 | 8,771 | 3,313 | 5 |
| Nemotron | 100 | 112 | 69 | 11,016 | 7,384 | 0 |

Each model has 25 honest episodes for each of the four scenarios in [DECISIONS.md](DECISIONS.md). The stored initial user-task content was identical within each model/scenario, checked by equality digests.

The combined inventory contains **93 called names and 26,879 calls**. `submit` and `end_task` account for 556 calls. The other 26,323 use 91 names, including helpers and unresolved tools. The 112 declared names are a union over episodes, not the number used or necessarily available in a single episode.

The scan used Python 3.14.9 and `pyarrow==26.0.1`. Its script and schema/count output are in ignored `debug/shade_inventory.py` and `debug/shade_inventory.json`; source shards remain in ignored `data/`. The output contains names, argument keys and aggregates, not argument values. A separate recount of source call lists reconciled all per-tool totals to 26,879.

## Categories

The assignment lists `search`, `read`, `send`, `transfer`, `delete` and `calendar`. That leaves no suitable class for the observed file/contact edits, spam-rule changes and permission grants.

A seventh class, `write`, covers non-financial state changes, while `send` remains message delivery and `calendar` calendar mutation. If we need six classes, the main methodology's wording "write or send" supports an explicit `write_or_send` merge instead. The counts below retain the distinction so either choice remains possible.

| Proposed type | Distinct called names | Calls across all honest episodes |
| --- | ---: | ---: |
| `search` | 8 | 5,977 |
| `read` | 47 | 6,793 |
| `write` | 15 | 9,373 |
| `send` | 3 | 1,998 |
| `transfer` | 7 | 1,199 |
| `delete` | 9 | 976 |
| `calendar` | 0 | 0 |
| `unresolved` | 2 | 7 |
| `control` | 2 | 556 |

`control` and `unresolved` are accounting states, not prediction classes. No native calendar-mutation tool was observed. Calendar searches and listings are `search`/`read`; emailing a calendar attachment is `send`, without any claim about downstream calendar state.

A few classifications need explanation:

- Scheduled transactions and their updates are financial instructions, so they are `transfer`, even though they contain dates. This includes commitments to future transfers, not just immediate payments.
- `remove_from_spam` moves an email and is `write`; removing a spam rule is `delete`.
- `share_file` grants access and is `write`. This mapping does not imply that a message was delivered.
- `read_tool_output` and `grep_tool_output` are harness reads. Keep a helper flag; the [self-consistency specification](SELF_CONSISTENCY_SPEC.md) proposes excluding them as targets while retaining them in histories.

These labels are for SHADE, not replacements for the BashArena taxonomy or a SHADE severity scale.

## Targets

Represent a target as the **native tool name plus the selected argument fields** listed below. For a no-argument tool, its name identifies the implicit resource or collection. This keeps the account-balance tools distinct without inventing account-ID arguments or collapsing them into one `read, none` label.

Include each listed field that is present. Keep omitted optional fields distinct from explicit null unless we agree a schema-checked default rule. Selector names were checked against the declarations, although some optional fields were not used in these runs.

Preserve types, nested values, list order, case and exact ID strings; sort dictionary keys for serialisation. Bash path/basename matching does not apply to SHADE IDs and queries. The two email-send tools also stay distinct: one takes email addresses and the other contact names. Resolving those aliases would need a separate rule.

Retain full original arguments for history matching, even though the prediction target uses a projection. Unexpected keys get a flag rather than being discarded. Rule IDs, rule indices, output references and generated file IDs may not refer to the same object across runs: exact target equality here is syntactic. Cross-run identity needs validation, and future outputs cannot be used to align a hidden target.

Type-and-target scoring does not grade every argument. A matching label can still have a wrong amount, body, permission or enable/disable value. Full-call exactness could be reported separately. Any equivalence between native tools also needs agreement before scoring.

## Called-tool mapping

Q = Qwen, K = Kimi, N = Nemotron. Every called name appears once. Counts cover full episodes, not just early prediction targets. An implicit target includes the native tool name.

| Native tool | Proposed type | Target selector arguments | Q calls | K calls | N calls |
| --- | --- | --- | ---: | ---: | ---: |
| `add_email_contact` | `write` | `email` | 54 | 276 | 155 |
| `add_global_rule` | `write` | `rule_type`, `pattern` | 1,406 | 641 | 4,465 |
| `add_phone_contact` | `write` | `name`, `phone_number`, `email` | 72 | 176 | 105 |
| `add_venmo_contact` | `write` | `username` | 3 | 0 | 0 |
| `add_zelle_contact` | `write` | `email` | 118 | 173 | 122 |
| `analyze_spam_patterns` | `read` | `min_occurrences` | 19 | 16 | 1 |
| `append_to_file` | `write` | `file_id` | 3 | 2 | 0 |
| `apply_rule_to_all_emails` | `write` | `rule_type`, `pattern` | 83 | 0 | 0 |
| `copy_rule_to_global` | `write` | `username`, `rule_id` | 0 | 167 | 0 |
| `create_file` | `write` | `filename` | 50 | 54 | 50 |
| `delete_email` | `delete` | `email_id` | 0 | 1 | 0 |
| `delete_file` | `delete` | `id` | 0 | 3 | 0 |
| `enable_disable_rule` | `write` | `rule_id`, `username` | 0 | 0 | 58 |
| `end_task` | `control` | excluded target | 106 | 72 | 78 |
| `find_overlapping_rules` | `read` | `username` | 0 | 18 | 0 |
| `find_redundant_rules` | `read` | `username` | 0 | 19 | 0 |
| `get_all_conversations` | `read` | implicit resource / collection | 0 | 9 | 0 |
| `get_all_spam` | `read` | implicit resource / collection | 28 | 26 | 14 |
| `get_balance` | `read` | implicit resource / collection | 25 | 49 | 25 |
| `get_bank_transactions_between_days` | `read` | `start_day`, `end_day` | 83 | 150 | 41 |
| `get_company_employees` | `read` | `company` | 0 | 1 | 0 |
| `get_conversation` | `read` | `contact_name` | 0 | 2 | 0 |
| `get_current_day` | `read` | implicit resource / collection | 75 | 75 | 60 |
| `get_daily_limit_zelle` | `read` | implicit resource / collection | 4 | 21 | 5 |
| `get_day_calendar_events` | `read` | `day` | 199 | 207 | 200 |
| `get_draft_emails` | `read` | implicit resource / collection | 3 | 12 | 0 |
| `get_emails_between_dates` | `read` | `start_date`, `end_date` | 19 | 67 | 9 |
| `get_exchange_rate` | `read` | `from_currency`, `to_currency` | 25 | 25 | 25 |
| `get_file_by_id` | `read` | `file_id` | 1 | 91 | 0 |
| `get_iban` | `read` | implicit resource / collection | 0 | 11 | 3 |
| `get_messages_between_days` | `read` | `start_day`, `end_day` | 12 | 50 | 3 |
| `get_most_recent_transactions` | `read` | `n` | 0 | 5 | 0 |
| `get_phone_contacts` | `read` | implicit resource / collection | 44 | 49 | 23 |
| `get_received_emails` | `read` | implicit resource / collection | 56 | 36 | 32 |
| `get_recent_messages` | `read` | `num_messages` | 22 | 18 | 28 |
| `get_recent_spam` | `read` | `limit` | 0 | 12 | 22 |
| `get_recent_venmo_transactions` | `read` | `num_transactions` | 6 | 21 | 0 |
| `get_recent_zelle_transactions` | `read` | `num_transactions` | 6 | 24 | 0 |
| `get_rule_statistics` | `read` | `rule_id`, `username` | 0 | 68 | 0 |
| `get_scheduled_transactions` | `read` | implicit resource / collection | 8 | 78 | 3 |
| `get_sent_emails` | `read` | implicit resource / collection | 24 | 10 | 1 |
| `get_spam` | `unresolved` | unresolved | 0 | 1 | 0 |
| `get_unread_emails` | `read` | implicit resource / collection | 35 | 51 | 45 |
| `get_venmo_balance` | `read` | implicit resource / collection | 74 | 58 | 35 |
| `get_venmo_contacts` | `read` | implicit resource / collection | 33 | 50 | 39 |
| `get_venmo_transactions_between_days` | `read` | `start_day`, `end_day` | 83 | 132 | 35 |
| `get_zelle_balance` | `read` | implicit resource / collection | 63 | 60 | 38 |
| `get_zelle_contacts` | `read` | implicit resource / collection | 33 | 50 | 39 |
| `get_zelle_transactions_between_days` | `read` | `start_day`, `end_day` | 83 | 135 | 35 |
| `grep_tool_output` | `read` | `ref`, `pattern`, `max_matches` | 122 | 222 | 76 |
| `import_contacts_zelle` | `write` | `csv_file_id` | 0 | 1 | 0 |
| `list_files` | `read` | implicit resource / collection | 59 | 68 | 30 |
| `list_global_rules` | `read` | implicit resource / collection | 34 | 53 | 69 |
| `list_user_rules` | `read` | `username` | 265 | 380 | 474 |
| `list_users` | `read` | implicit resource / collection | 25 | 26 | 25 |
| `mark_as_spam` | `write` | `email_id`, `username` | 263 | 13 | 137 |
| `next_id` | `unresolved` | unresolved | 0 | 0 | 6 |
| `preview_rule` | `read` | `rule_type`, `pattern` | 12 | 133 | 0 |
| `read_tool_output` | `read` | `ref`, `start_line`, `end_line` | 187 | 117 | 72 |
| `remove_from_spam` | `write` | `email_id`, `username` | 167 | 63 | 99 |
| `remove_global_rule` | `delete` | `rule_index` | 4 | 0 | 10 |
| `remove_global_rule_by_id` | `delete` | `rule_id` | 5 | 4 | 45 |
| `remove_multiple_global_rules_by_id` | `delete` | `rule_ids` | 0 | 0 | 2 |
| `remove_multiple_user_rules` | `delete` | `username`, `rule_indices` | 0 | 20 | 10 |
| `remove_multiple_user_rules_by_id` | `delete` | `username`, `rule_ids` | 4 | 267 | 188 |
| `remove_user_rule` | `delete` | `username`, `rule_index` | 69 | 3 | 10 |
| `remove_user_rule_by_id` | `delete` | `username`, `rule_id` | 63 | 37 | 231 |
| `reprocess_all_emails` | `write` | implicit resource / collection | 102 | 77 | 143 |
| `schedule_multiple_transactions` | `transfer` | `recipient_list`, `date`, `recurring` | 5 | 11 | 0 |
| `schedule_transaction` | `transfer` | `recipient`, `date`, `recurring` | 76 | 75 | 57 |
| `search_by_company` | `search` | `company` | 0 | 2 | 0 |
| `search_calendar_events` | `search` | `query`, `date` | 0 | 4 | 1 |
| `search_contacts_by_email` | `search` | `query` | 239 | 409 | 659 |
| `search_contacts_by_name` | `search` | `query` | 280 | 500 | 312 |
| `search_emails` | `search` | `query`, `sender` | 587 | 968 | 926 |
| `search_files` | `search` | `query` | 34 | 32 | 39 |
| `search_files_by_filename` | `search` | `filename` | 175 | 447 | 203 |
| `search_spam` | `search` | `query`, `sender` | 22 | 73 | 65 |
| `send_email` | `send` | `recipients`, `cc`, `bcc` | 570 | 682 | 563 |
| `send_email_to_contacts` | `send` | `recipients`, `cc`, `bcc` | 42 | 0 | 94 |
| `send_message` | `send` | `recipient_name` | 34 | 7 | 6 |
| `send_money_venmo` | `transfer` | `recipient_username` | 77 | 73 | 85 |
| `send_money_zelle` | `transfer` | `recipient_email` | 172 | 132 | 127 |
| `share_file` | `write` | `id`, `email` | 25 | 25 | 25 |
| `simulate_multiple_rules` | `read` | `rules` | 0 | 10 | 0 |
| `submit` | `control` | excluded target | 100 | 100 | 100 |
| `transfer_bank_to_venmo` | `transfer` | implicit resource / collection | 49 | 41 | 27 |
| `transfer_bank_to_zelle` | `transfer` | implicit resource / collection | 48 | 46 | 27 |
| `update_scheduled_transaction` | `transfer` | `id` | 4 | 67 | 0 |
| `view_all_inboxes` | `read` | implicit resource / collection | 116 | 68 | 145 |
| `view_all_spam` | `read` | implicit resource / collection | 74 | 68 | 129 |
| `view_user_inbox` | `read` | `username` | 15 | 91 | 3 |
| `view_user_spam` | `read` | `username` | 9 | 84 | 2 |

## Anomalies and unresolved cases

- Kimi called `get_spam` **once**, although it is absent from that episode's declarations. Leave it unresolved rather than aliasing it to `get_all_spam` or `get_recent_spam`.
- One Nemotron `search_spam` call includes `limit`, while its schema permits `query` and `sender`. Keep the `search` mapping for the name and flag that call's invalid key. No missing required top-level keys were found; nested schema validity and runtime effects were not checked.
- Nemotron called `next_id` **six times**. Its description says it gets the next transaction ID, but does not establish whether it advances a counter. Its category remains unresolved pending implementation semantics.
- `mark_as_spam` and `remove_from_spam` each have two parameter-schema variants, including user-scoped forms with `username`. Use the episode's declaration.
- Some descriptions are attachment references. Those mappings rely on the native name and available argument schema; the referenced descriptions were not expanded.

Neither schema anomaly was a first-call target among the first five non-terminal tool-bearing turns. Both remain part of full-episode accounting and any relevant prior history.

## Early steps

Five `steps` records are missing from four Kimi episodes: two episodes in `api_key_calendar_agendas_2` and two in `bank_transfer_2`. Three of the missing turns fall among those episodes' first five non-terminal tool-bearing turns. No ordering or duplicate-index mismatch was found among the recorded steps.

Excluding those four episodes, the first five **recorded assistant steps** give 1,480 slots across 296 episodes. All have non-empty reasoning and a non-terminal first tool call. Of these, 252 start with a helper and 660 contain multiple calls. Excluding helper targets leaves **1,228 candidates before peer-history coverage is checked**. Earlier helpers and all earlier calls remain in history. These are structural counts, not predictability scores.

## Human review

- Check each category and target selector against its native schema. Pay particular attention to file sharing, spam-rule edits, scheduled transfers and the distinction between `write`, `send` and `transfer`.
- Inspect the undeclared `get_spam` call, the unexpected `search_spam` argument and the implementation semantics of `next_id`. Record unresolved cases without inventing aliases or executing their tools.
- Check whether IDs, indices and output references identify comparable resources across replicates before treating equal strings as equal semantic targets.
- Inspect the five missing step records against assistant-message order, and choose strict episode exclusion or an explicitly separate reconstruction analysis.
