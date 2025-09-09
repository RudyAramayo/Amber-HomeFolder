import math
import time
import lcm

lcm_recv_channel = "Amber_ArmStatus"
lcm_send_channel = "Amber_PosCmd"
from lcmTypes.posCmd_t import posCmd_t

freq = 50
speed = 2
period = speed * 4


def calculate_sin(t, t_now):
    # Calculate the sine function value
    y = math.sin((2 * math.pi / t) * t_now)
    return y


def sendLCM(pos):
    pos = pos*57.29
    msg = posCmd_t()
    msg.jointTarget = (pos, pos, pos, pos, pos, pos, pos)
    lc = lcm.LCM()
    lc.publish(lcm_send_channel, msg.encode())


print(f"Generating test: Frequency = {freq}, Period={period}: rotate 1 radians in {speed} seconds")
# Example usage:
t = period  # Period of the sine function
t_now = 0  # Current time
time_start = time.perf_counter()
while True:
    time_end = time.perf_counter()
    time_consumed = time_end - time_start
    if time_consumed > (1 / freq):
        time_start = time_end
        t_now += time_consumed
        y_value = calculate_sin(t, t_now)
        sendLCM(y_value)
        print(f"  T={t_now:.3f} pos={y_value:+.3f}", end="\r")
