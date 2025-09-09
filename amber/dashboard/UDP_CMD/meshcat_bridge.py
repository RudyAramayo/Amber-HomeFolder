import socket
from ctypes import *

import app


IP_ADDR = "127.0.0.1"


class robot_joint_position(Structure):  # ctypes struct for send
    _pack_ = 1  # Override Structure align
    _fields_ = [("cmd_no", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ]


class robot_mode_data(Structure):  # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("pos_1", c_float),
                ("pos_2", c_float),
                ("pos_3", c_float),
                ("pos_4", c_float),
                ("pos_5", c_float),
                ("pos_6", c_float),
                ("pos_7", c_float),
                ("pos_8", c_float)
                ]
async def push_status():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Standard socket processes
    try:
        s.bind(("0.0.0.0", 23209))
    except OSError:
        return
    payloadS = robot_mode_data(1, 8, 114514, app.param.jointNow[0], app.param.jointNow[1], app.param.jointNow[2], app.param.jointNow[3], app.param.jointNow[4], app.param.jointNow[5], app.param.jointNow[6], app.param.jointNow[7])  # Fill struct for send with numbers
    s.sendto(payloadS, (IP_ADDR, 23210))  # Default port is 25001
    #print("Sending: cmd_no={:d}, "
    #      "length={:d}, counter={:d},".format(payloadS.cmd_no,
    #                                          payloadS.length,
    #                                          payloadS.counter, ))
    #s.settimeout(2)
    #data, addr = s.recvfrom(1024)
    #try:
    #    data, addr = s.recvfrom(1024)  # Need receive return
    #    #print("Receiving: ", data.hex())
    #    payloadR = robot_mode_data.from_buffer_copy(data)  # Convert raw data into ctypes struct to print
    #    app.param.jointNow[0] = payloadR.pos_1
    #    app.param.jointNow[1] = payloadR.pos_2
    #    app.param.jointNow[2] = payloadR.pos_3
    #    app.param.jointNow[3] = payloadR.pos_4
    #    app.param.jointNow[4] = payloadR.pos_5
    #    app.param.jointNow[5] = payloadR.pos_6
    #    app.param.jointNow[6] = payloadR.pos_7
    #    app.param.jointNow[7] = payloadR.pos_8
    #    app.param.ROS_Online_Flag = True
    #    app.param.cartesianNow[0] = payloadR.X_pos
    #    app.param.cartesianNow[1] = payloadR.Y_pos
    #    app.param.cartesianNow[2] = payloadR.Z_pos
    #except socket.timeout:
    #    app.param.ROS_Online_Flag = False
    #