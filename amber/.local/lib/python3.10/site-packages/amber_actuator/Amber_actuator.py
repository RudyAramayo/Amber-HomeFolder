import socket
from ctypes import *


class Udp2canFloat(Structure):  # ctypes struct for receive
    _pack_ = 1  # Override Structure align
    _fields_ = [("verb", c_uint8),
                ("cmd", c_uint8),
                ("actuator_ID", c_uint8),
                ("data", c_float),  # ctypes array
                ]
def float_data_through_udp(ip_addr,cmd,verb,data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 12321))
    fcandata = Udp2canFloat()
    fcandata.verb = 1
    fcandata.cmd = cmd
    fcandata.actuator_ID = id
    fcandata.data = data
    s.sendto(fcandata, (ip_addr, 24001))
    data, addr = s.recvfrom(1024)
    # print("Receiving: ", data.hex())
    payload_r = Udp2canFloat.from_buffer_copy(data)
    return [payload_r.actuator_ID, payload_r.data]


class Position:
    def __init__(self, ip_addr="127.0.0.1"):
        #self._position = None
        self.ip_addr = ip_addr

    @property
    def ki(self):
        reuslt = float_data_through_udp(self.ip_addr,12,0,0)
        #return self._position

    @ki.setter
    def ki(self, data):
        reuslt = float_data_through_udp(self.ip_addr,12,1,data)
        return reuslt
        #self._position= target

class AmberActuator:
    position = Position("127.0.0.1")

if __name__ == "__main__":
    amber_robot = AmberActuator()
