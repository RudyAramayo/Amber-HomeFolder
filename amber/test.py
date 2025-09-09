import time
import amber_api.cmd_4
while True:
    amber_api.cmd_4.move_joint("127.0.0.1",26001,[-1,-1,-1,-1,-1,-1,-1,-1],3)
    print("Moving to [-1,-1,-1,-1,-1,-1,-1]")
    time.sleep(4)
    amber_api.cmd_4.move_joint("127.0.0.1",26001,[1,1,1,1,1,1,1,1],3)
    print("Moving to [ 1, 1, 1, 1, 1, 1, 1]")
    time.sleep(4)

