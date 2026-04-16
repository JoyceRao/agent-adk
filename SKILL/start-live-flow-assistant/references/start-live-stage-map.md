# startLive 关键阶段映射（按最新 startLive.puml）

- IM触发实时呼叫（Entry A）：
  `im_event_received` -> `im_event_route_realtime_call` -> `realtime_call_handler_enter` -> `realtime_call_background_notify/realtime_call_foreground_present` -> `realtime_popup_*`
- 恢复呼叫数据（Entry B）：
  `recover_check_start` -> `recover_api_failure/recover_handle_result` -> `recover_go_realtime_popup`
- 实时弹窗关闭路径：
  `realtime_popup_dismiss_cancel` / `realtime_popup_dismiss_remindStart` / `realtime_popup_dismiss`
- 弹窗点击开播（预创建）：
  `realtime_click_enter_room_denied_auth/realtime_click_enter_room` -> `precreate_start` -> `precreate_success/precreate_sign_data_nil/precreate_disabled/precreate_failure`
- 跳转直播页并加载直播数据：
  `jump_recover_enter` -> `jump_recover_im_*` -> `jump_recover_present_livevc/jump_recover_livevc_presented` ->
  `livevc_view_did_load` -> `livevc_enter_room_success/livevc_enter_room_fail` ->
  `livevc_update_room_status_*` -> `livevc_load_room_info_failure/livevc_load_live_room_data_finish`

完整映射与流程顺序以 `tools/shared.py` 中 `START_LIVE_STAGE_ORDER` 为准。
