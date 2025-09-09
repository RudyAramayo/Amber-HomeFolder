#!/bin/bash
var="E"
var=$(ls /dev|grep "ttyACM" )
echo "Initializing two devices: "${var}

sudo slcand -o -c -s8 /dev/ttyACM3 can0
sudo ifconfig can0 up
sudo ifconfig can0 txqueuelen 1000

sudo slcand -o -c -s8 /dev/ttyACM4 can1
sudo ifconfig can1 up
sudo ifconfig can1 txqueuelen 1000
