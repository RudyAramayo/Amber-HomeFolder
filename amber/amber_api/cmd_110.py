
import socket
from ctypes import *
'''
An example for Cartesian control by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#4-cartesian-control

'''

#IP_ADDR = "127.0.0.1"  # ROS master's IP address


class robot_joint_position(Structure):                              # ctypes struct for send
    _pack_ = 1                                                      # Override Structure align
    _fields_ = [("cmd_no", c_uint16),                               # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("joint_id", c_uint32),
                ]


class robot_mode_data(Structure):                                   # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint16*7),
                ]


def get_work_mode(IP_ADDR="127.0.0.1", port=26001):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #s.bind(("0.0.0.0", 12430))
    payloadS = robot_joint_position(110, 12, 0, 8)
    s.sendto(payloadS, (IP_ADDR, port))
    s.settimeout(1)

    try:                                    
        data, addr = s.recvfrom(1024)                                       # Need receive return
        #print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)
        return payloadR.respond
    except socket.timeout:
        return -1
