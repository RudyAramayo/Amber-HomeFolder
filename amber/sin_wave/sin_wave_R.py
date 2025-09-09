import math
import time
import lcm

lcm_recv_channel = "Right_ArmStatus"
lcm_send_channel = "Right_PosCmd"
from lcmTypes.posCmd_t import posCmd_t
from lcmTypes.armStatus_t import armStatus_t

freq = 50
speed = 2
period = speed * 4


def calculate_sin(t, t_now):
    # Calculate the sine function value
    y = math.sin((2 * math.pi / t) * t_now)
    return y


def sendLCM(pos):
    pos = pos * 57.29
    msg = posCmd_t()
    msg.jointTarget = (pos, pos, pos, pos, pos, pos, pos)
    lc = lcm.LCM()
    lc.publish(lcm_send_channel, msg.encode())


def my_handler(channel, data):
    msg = armStatus_t.decode(data)
    print(f"{msg.jointPosition[0]:+.3f} "
          f"{msg.jointPosition[1]:+.3f} "
          f"{msg.jointPosition[2]:+.3f} "
          f"{msg.jointPosition[3]:+.3f} "
          f"{msg.jointPosition[4]:+.3f} "
          f"{msg.jointPosition[5]:+.3f} "
          f"{msg.jointPosition[6]:+.3f}", end="\r")


print(f"Generating test: Frequency = {freq}, Period={period}: rotate 1 radians in {speed} seconds")
# Example usage:
t = period  # Period of the sine function
t_now = 0  # Current time
time_start = time.perf_counter()
lc = lcm.LCM()
subscription = lc.subscribe(lcm_recv_channel, my_handler)

try:
    while True:
        # Handle LCM Once
        lc.handle()

        time_end = time.perf_counter()
        time_consumed = time_end - time_start

        if time_consumed > (1 / freq):
            time_start = time_end
            t_now += time_consumed
            y_value = calculate_sin(t, t_now)
            sendLCM(y_value)  # Here we send the command
            # print(f"  T={t_now:.3f} pos={y_value:+.3f}", end="\r")
except KeyboardInterrupt:
    pass
