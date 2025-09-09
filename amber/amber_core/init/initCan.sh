#!/bin/bash

# Grep all dmesg lines containing "ACM"
dmesg | grep "ACM" > acm_entries.txt

# Check if any ACM entries were found
if [[ ! -s acm_entries.txt ]]; then
  echo "No ACM entries found in dmesg."
  exit 1
fi
echo "=========== Finding Device(s) ==========="
# Extract and process entries
while read -r line; do
  if [[ $line =~ ACM[[:space:]]*([0-9]+) ]]; then
        # Print the matched part
        ACM_name=${BASH_REMATCH[0]}
    fi
  # Use regex to find one digit before and after "-"
  if [[ $line =~ ([0-9])-([0-9]) ]]; then
    before=${BASH_REMATCH[1]}
    after=${BASH_REMATCH[2]}
    usb_string="usb ${before}-${after}"
    
    # Query dmesg with the usb_string and filter out lines containing "SerialNumber"
    dmesg | grep "$usb_string" | grep  "SerialNumber:"| while read -r serial_line; do
    serial_number=$(echo "$serial_line" | sed -n 's/.*SerialNumber: //p')
    echo "Device: $ACM_name SerialNumber: $serial_number"
    echo "$ACM_name,$serial_number" >> CAN_Devices.txt
  done
  else
    echo "No matching pattern found in entry: $line"
  fi
done < acm_entries.txt

# Clean up temporary file
rm acm_entries.txt
declare -A str1_str2
echo "========= Configuring Device(s) ========="
while IFS=, read -r str1 str2; do
    # Trim spaces around the strings
    str1=$(echo "$str1" | xargs)
    str2=$(echo "$str2" | xargs)

    # Only add to array if both str1 and str2 are not empty
    if [[ -n "$str1" && -n "$str2" ]]; then
        str1_str2["$str2"]="$str1"
    fi
done < CAN_Devices.txt

# Read file2 and find matching str2 values to output str1 and str3 pairs
while IFS=, read -r str2 str3; do
    # Trim spaces around the strings
    str2=$(echo "$str2" | xargs)
    str3=$(echo "$str3" | xargs)
    # Check if str2 is in the associative array
    if [[ -n "${str1_str2["$str2"]}" ]]; then
        str1="${str1_str2["$str2"]}"
        echo "Config $str3 with $str1, SN = $str2"
        slcand -o -c -s8 /dev/tty${str1} $str3
        ifconfig ${str3} up
        ifconfig ${str3} txqueuelen 1000
    fi
done < SerialNumber.txt

rm CAN_Devices.txt
