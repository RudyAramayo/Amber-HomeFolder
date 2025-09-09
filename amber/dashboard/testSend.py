import lcm
from lcmTypes import rosCommand_t

msg = rosCommand_t()
msg.jointTarget = (0,0,0,0,0,0,0)

lc = lcm.LCM()
lc.publish("RosCommand", msg.encode())
