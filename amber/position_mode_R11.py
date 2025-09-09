import amber_api.cmd_10
#result = amber_api.cmd_10.on_position_mode(IP_ADDR="127.0.0.1", port=26001)
#if result != -1:
#    pass
#else:
#    print("Socket Timeout! L arm")

result = amber_api.cmd_10.on_position_mode(IP_ADDR="127.0.0.1", port=26002)
if result != -1:
    pass
else:
    print("Socket Timeout! R arm")
