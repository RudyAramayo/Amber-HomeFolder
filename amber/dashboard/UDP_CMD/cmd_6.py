import socket
from ctypes import *
import time
import app

'''
An example for Cartesian control by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#4-cartesian-control

'''

IP_ADDR = "127.0.0.1"  # ROS master's IP address


class robot_joint_position(Structure):  # ctypes struct for send
    _pack_ = 1  # Override Structure align
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("xyz", c_float * 3),  # ctypes array
                ("rpy", c_float * 3),
                ("arm_angle", c_float),
                ("time", c_float),
                ]


class robot_mode_data(Structure):  # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint8),
                ]

deg2arc=0.017453292519943295
async def Cartesian_CTRL(carTarget, exec_time,s):
    tmp_1 = robot_joint_position()
    tmp_1.cmd_no = 6
    tmp_1.length = 40
    tmp_1.xyz[0] = carTarget[0]/1000
    tmp_1.xyz[1] = carTarget[1]/1000
    tmp_1.xyz[2] = carTarget[2]/1000
    tmp_1.rpy[0] = carTarget[3]/57.30
    tmp_1.rpy[1] = carTarget[4]/57.30
    tmp_1.rpy[2] = carTarget[5]/57.30
    tmp_1.arm_angle = 0
    tmp_1.time = exec_time
    print(f"{tmp_1.xyz[0]},{tmp_1.xyz[1]},{tmp_1.xyz[2]},{tmp_1.rpy[0]},{tmp_1.rpy[0]},{tmp_1.rpy[0]},")
    #s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #s.bind(("0.0.0.0", 12326))
    s.sendto(tmp_1, (IP_ADDR, 26001))
    s.settimeout(15)
    try:
        data, addr = s.recvfrom(1024)
        print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)
        print("Received: cmd_no={:d}, length={:d}, "
          "counter={:d}, respond={:d}".format(payloadR.cmd_no,
                                              payloadR.length,
                                              payloadR.counter,
                                              payloadR.respond, ))
        return payloadR.respond
    except socket.timeout:
        return 4
#
