import ctypes
import socket
from ctypes import *

'''
A simple example for controlling a single joint move once by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#2-single-joint-move-once
C++ version:  https://github.com/MrAsana/C_Plus_API/tree/master/amber_gui_4_node
     
'''


class robot_joint_position(Structure):  # ctypes struct for send
    _pack_ = 1  # Override Structure align
    _fields_ = [("cmd_no", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("pos", c_float * 8),
                ("duration", c_float),
                ]


class robot_mode_data(Structure):  # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint8),
                ]


zero = [0, 0, 0, 0, 0, 0, 0, 0]


def move_joint(IP_ADDR="127.0.0.1", port=26001, pos=None, duration=5):
    if pos is None:
        pos = zero
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #s.bind(("0.0.0.0", 12404))
    buf = robot_joint_position()
    buf.cmd_no = 4
    buf.length = 44
    buf.counter = 114514
    buf.pos = (ctypes.c_float * len(pos))(*pos)
    buf.duration = duration
    s.sendto(buf, (IP_ADDR, port))

    s.settimeout(3)
    try:
        data, addr = s.recvfrom(1024)
        payloadR = robot_mode_data.from_buffer_copy(data)
        return payloadR
    except socket.timeout:
        return -1
