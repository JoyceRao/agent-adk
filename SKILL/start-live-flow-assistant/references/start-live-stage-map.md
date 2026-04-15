# startLive 关键阶段映射（摘要）

- 恢复链路：`recover_check_start` -> `recover_api_failure`
- IM 触发实时呼叫：`im_event_received` -> `im_event_route_realtime_call` -> `realtime_call_handler_enter` -> `realtime_call_foreground_present/realtime_call_background_notify`
- 弹窗点击开播：`realtime_click_enter_room` -> `precreate_start` -> `precreate_success/precreate_failure`
- 跳转直播页并加载：`jump_recover_enter` -> `jump_recover_present_livevc` -> `livevc_view_did_load` -> `livevc_enter_room_success` -> `livevc_load_room_info_failure/livevc_load_live_room_data_finish`

完整映射以 `tools/shared.py` 中 `START_LIVE_STAGE_ORDER` 为准。
