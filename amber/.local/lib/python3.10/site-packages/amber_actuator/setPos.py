import socket
from ctypes import *

'''
comment here
'''

def target(id, pos, ip="127.0.0.1"):
    class Udp2canFloat(Structure):  # ctypes struct for receive
        _pack_ = 1  # Override Structure align
        _fields_ = [("verb", c_uint8),
                    ("cmd", c_uint8),
                    ("actuator_ID", c_uint8),
                    ("data", c_float),  # ctypes array
                    ]

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    s.bind(("0.0.0.0", 12321))
    fcandata = Udp2canFloat()
    fcandata.verb = 1
    fcandata.cmd = 11
    fcandata.actuator_ID = id
    fcandata.data = pos
    s.sendto(fcandata, (ip, 24001))

    data, addr = s.recvfrom(1024)
    #print("Receiving: ", data.hex())
    payload_r = Udp2canFloat.from_buffer_copy(data)
    return [payload_r.actuator_ID,payload_r.data]
    #print("Received: verb={:d}, cmd={:d}, "
    #      "actuator_ID={:d}, data={:f}".format(payloadR.verb,
    #                                           payloadR.cmd,
    #                                           payloadR.actuator_ID,
    #                                           payloadR.data, ))
#