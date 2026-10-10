# Evidence index

| run dir | kind | args / goal | status | code | recoveries | interventions |
|---|---|---|---|---|---|---|
| `20261010T204549_discovery_member_savings_balance_7750ed` | discovery | Look up member {{member_id}} and read the current balance of their Pri | success |  |  |  |
| `20261010T204604_replay_member_savings_balance_8bf6f1` | replay | {"member_id": "[input:member_id]"} | rejected | NOT_APPROVED |  |  |
| `20261010T204605_replay_member_savings_balance_410fba` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T204608_replay_member_savings_balance_df4620` | replay | {"member_id": "[input:member_id]"} | business_outcome | RECORD_NOT_FOUND |  |  |
| `20261010T204610_replay_member_savings_balance_10f130` | replay | {"member_id": "[input:member_id]"} | business_outcome | ACCESS_DENIED |  |  |
| `20261010T204612_replay_member_savings_balance_15f087` | replay | {"member_id": "[input:member_id]"} | rejected | INVALID_ARGUMENTS |  |  |
| `20261010T204614_replay_member_savings_balance_5038ec` | replay | {"member_id": "[input:member_id]"} | success |  | system_notice |  |
| `20261010T204618_replay_member_savings_balance_0161fe` | replay | {"member_id": "[input:member_id]"} | success |  | session_expired |  |
| `20261010T204625_replay_member_savings_balance_17996e` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T204632_replay_member_savings_balance_37e117` | replay | {"member_id": "[input:member_id]"} | failed | APP_ERROR |  |  |
| `20261010T204636_replay_member_savings_balance_813a71` | replay | {"member_id": "[input:member_id]"} | success |  | transient:checkpoint_not_reached | stuck:resume by alice |
| `20261010T204701_replay_member_savings_balance_beecdb` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T204704_replay_member_savings_balance_5acbd1` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
