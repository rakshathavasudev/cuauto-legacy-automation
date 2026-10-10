# Evidence index

| run dir | command | tenant | args / goal | status | code | locators | recoveries | interventions |
|---|---|---|---|---|---|---|---|---|
| `20261010T204549_discovery_member_savings_balance_7750ed` | `discovery` | buffalo-teachers | Look up member {{member_id}} and read the current balance of their Pri | success |  |  |  |  |
| `20261010T204604_replay_member_savings_balance_8bf6f1` | `replay --version 4` | buffalo-teachers | {"member_id": "[input:member_id]"} | rejected | NOT_APPROVED |  |  |  |
| `20261010T204605_replay_member_savings_balance_410fba` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic |  |  |
| `20261010T204608_replay_member_savings_balance_df4620` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | business_outcome | RECORD_NOT_FOUND | semantic |  |  |
| `20261010T204610_replay_member_savings_balance_10f130` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | business_outcome | ACCESS_DENIED | semantic |  |  |
| `20261010T204612_replay_member_savings_balance_15f087` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | rejected | INVALID_ARGUMENTS |  |  |  |
| `20261010T204614_replay_member_savings_balance_5038ec` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic | system_notice |  |
| `20261010T204618_replay_member_savings_balance_0161fe` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic | session_expired |  |
| `20261010T204625_replay_member_savings_balance_17996e` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic |  |  |
| `20261010T204632_replay_member_savings_balance_37e117` | `replay` | buffalo-teachers | {"member_id": "[input:member_id]"} | failed | APP_ERROR | semantic |  |  |
| `20261010T204636_replay_member_savings_balance_813a71` | `replay --on-stuck escalate` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic | transient:checkpoint_not_reached | stuck:resume by alice |
| `20261010T204701_replay_member_savings_balance_beecdb` | `replay` | lakeshore-cu | {"member_id": "[input:member_id]"} | success |  | semantic+alias |  |  |
| `20261010T204704_replay_member_savings_balance_5acbd1` | `invoke` | buffalo-teachers | {"member_id": "[input:member_id]"} | success |  | semantic |  |  |

Commands that create no run directory (their output is in `demo_console.log`):

- `cuauto capabilities show legacy-corebank.member_savings_balance --version 4`
- `cuauto capabilities approve legacy-corebank.member_savings_balance --version 4 --by rakshatha`
- `cuauto operator list --all`
- `cuauto catalog`
