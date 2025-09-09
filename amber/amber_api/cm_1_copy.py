import socket
from ctypes import *

'''
A simple example for controlling a single joint move once by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#2-single-joint-move-once
C++ version:  https://github.com/MrAsana/C_Plus_API/tree/master/amber_gui_4_node
     
'''


class robot_joint_position(Structure):
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ]


class robot_mode_data(Structure):  # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("position", c_float * 8),
                ("speed", c_float * 8),  # Not implemented, reserved
                ("cartesian_position", c_float * 6),
                ("cartesian_speed", c_float * 6),  # Not implemented, reserved
                ("Arm_Angle", c_float),  # Not implemented, reserved
                ]


def get_status(IP_ADDR="127.0.0.1", port=26001):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #s.bind(("0.0.0.0", 12401))
    payloadS = robot_joint_position(1, 8, 11)
    s.sendto(payloadS, (IP_ADDR, port))
    s.settimeout(3)
    try:
        data, addr = s.recvfrom(1024)
        payloadR = robot_mode_data.from_buffer_copy(data)
        position_now = [0, 0, 0, 0, 0, 0, 0, 0]
        for i in range(8):
            position_now[i] = payloadR.position[i]
        return position_now
    except socket.timeout:
        return -1
