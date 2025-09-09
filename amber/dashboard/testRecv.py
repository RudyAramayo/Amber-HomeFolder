import lcm
from lcmTypes import rosData_t

def my_handler(channel, data):
    msg = rosData_t.decode(data)
    print("Received message on channel \"%s\"" % channel)
    for i in range (7):
        print("   position    = %s" % str(msg.jointPosition[i]))

lc = lcm.LCM()
subscription = lc.subscribe("RosData", my_handler)

try:
    while True:
        lc.handle()
except KeyboardInterrupt:
    pass
