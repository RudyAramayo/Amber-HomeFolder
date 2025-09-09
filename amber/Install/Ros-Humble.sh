#!/bin/bash 
sudo sed -i 's/#$nrconf{restart} = '"'"'i'"'"';/$nrconf{restart} = '"'"'a'"'"';/g' /etc/needrestart/needrestart.conf
sudo apt -y update
sudo apt -y upgrade
sudo apt -y install curl
curl https://raw.githubusercontent.com/Tiryoh/ros2_setup_scripts_ubuntu/main/ros2-humble-desktop-main.sh | bash
sudo apt -y install ros-humble-moveit
sudo apt -y install git
source /opt/ros/humble/setup.bash
rosdep update
sudo apt -y install ament-cmake
sudo apt -y install ros-humble-joint-state-controller
sudo apt -y install ros-humble-effort-controllers
sudo apt -y install ros-humble-position-controllers
sudo apt -y install ros-humble-joint-state-broadcaster
sudo apt -y install ros-humble-joint-trajectory-controller
sudo apt -y install ros-humble-controller-manager
sudo apt -y install ros-humble-joint-state-publisher-gui
sudo apt -y install ros-humble-xacro
sudo apt-get install ros-humble-gripper*

echo ""
echo "|      ___           ___           ___           ___           ___     |"
echo "|     /\  \         /\__\         /\  \         /\  \         /\  \    |"
echo "|    /::\  \       /::|  |       /::\  \       /::\  \       /::\  \   |"
echo "|   /:/\:\  \     /:|:|  |      /:/\:\  \     /:/\:\  \     /:/\:\  \  |"
echo "|  /::\~\:\  \   /:/|:|__|__   /::\~\:\__\   /::\~\:\  \   /::\~\:\  \ |"
echo "| /:/\:\ \:\__\ /:/ |::::\__\ /:/\:\ \:|__| /:/\:\ \:\__\ /:/\:\ \:\__\|"
echo "| \/__\:\/:/  / \/__/~~/:/  / \:\~\:\/:/  / \:\~\:\ \/__/ \/_|::\/:/  /|"
echo "|      \::/  /        /:/  /   \:\ \::/  /   \:\ \:\__\      |:|::/  / |"
echo "|      /:/  /        /:/  /     \:\/:/  /     \:\ \/__/      |:|\/__/  |"
echo "|     /:/  /        /:/  /       \::/__/       \:\__\        |:|  |    |"
echo "|     \/__/         \/__/         ~~            \/__/         \|__|    |"
echo -e "\e[1;32m\e[0m"

# echo -e "\e\033[42;37m ros2 launch amber_b1_moveit_config demo.launch.py \e[0m"
echo -e "\e[1;36m Version: 1.1 Dec.19th/2022-humble  \e[0m"
