# Evidence index

| run dir | kind | args / goal | status | code | recoveries | interventions |
|---|---|---|---|---|---|---|
| `20261010T203014_discovery_member_savings_balance_79a89c` | discovery | Look up member {{member_id}} and read the current balance of their Pri | success |  |  |  |
| `20261010T203113_discovery_member_savings_balance_65405c` | discovery | Look up member {{member_id}} and read the current balance of their Pri | success |  |  |  |
| `20261010T203127_replay_member_savings_balance_27708d` | replay | {"member_id": "[input:member_id]"} | rejected | NOT_APPROVED |  |  |
| `20261010T203129_replay_member_savings_balance_9546e5` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T203132_replay_member_savings_balance_b8633f` | replay | {"member_id": "[input:member_id]"} | business_outcome | RECORD_NOT_FOUND |  |  |
| `20261010T203134_replay_member_savings_balance_17a210` | replay | {"member_id": "[input:member_id]"} | business_outcome | ACCESS_DENIED |  |  |
| `20261010T203137_replay_member_savings_balance_65deaa` | replay | {"member_id": "[input:member_id]"} | rejected | INVALID_ARGUMENTS |  |  |
| `20261010T203138_replay_member_savings_balance_5ee6a9` | replay | {"member_id": "[input:member_id]"} | success |  | system_notice |  |
| `20261010T203142_replay_member_savings_balance_cd394a` | replay | {"member_id": "[input:member_id]"} | success |  | session_expired |  |
| `20261010T203147_replay_member_savings_balance_94129f` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T203154_replay_member_savings_balance_472755` | replay | {"member_id": "[input:member_id]"} | failed | APP_ERROR |  |  |
| `20261010T203159_replay_member_savings_balance_037a98` | replay | {"member_id": "[input:member_id]"} | success |  | transient:checkpoint_not_reached | stuck:resume by alice |
| `20261010T203224_replay_member_savings_balance_8c4920` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20261010T203227_replay_member_savings_balance_896fb5` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
