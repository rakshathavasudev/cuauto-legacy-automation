# Evidence index

| run dir | kind | args / goal | status | code | recoveries | interventions |
|---|---|---|---|---|---|---|
| `20260930T215316_discovery_member_savings_balance_d1f8a4` | discovery | Look up member {{member_id}} and read the current balance of their Pri | success |  |  |  |
| `20260930T215323_replay_member_savings_balance_173229` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20260930T215323_replay_member_savings_balance_a9737b` | replay | {"member_id": "[input:member_id]"} | rejected | NOT_APPROVED |  |  |
| `20260930T215325_replay_member_savings_balance_3a03b9` | replay | {"member_id": "[input:member_id]"} | business_outcome | RECORD_NOT_FOUND |  |  |
| `20260930T215327_replay_member_savings_balance_8f198c` | replay | {"member_id": "[input:member_id]"} | business_outcome | ACCESS_DENIED |  |  |
| `20260930T215329_replay_member_savings_balance_5d3fba` | replay | {"member_id": "[input:member_id]"} | rejected | INVALID_ARGUMENTS |  |  |
| `20260930T215330_replay_member_savings_balance_9eecdb` | replay | {"member_id": "[input:member_id]"} | success |  | system_notice |  |
| `20260930T215333_replay_member_savings_balance_497ad0` | replay | {"member_id": "[input:member_id]"} | success |  | session_expired |  |
| `20260930T215337_replay_member_savings_balance_6aa899` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20260930T215343_replay_member_savings_balance_85bdef` | replay | {"member_id": "[input:member_id]"} | failed | APP_ERROR |  |  |
| `20260930T215346_replay_member_savings_balance_f105a9` | replay | {"member_id": "[input:member_id]"} | success |  | transient:checkpoint_not_reached | stuck:resume by alice |
| `20260930T215409_replay_member_savings_balance_5ed431` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
| `20260930T215412_replay_member_savings_balance_2d8268` | replay | {"member_id": "[input:member_id]"} | success |  |  |  |
