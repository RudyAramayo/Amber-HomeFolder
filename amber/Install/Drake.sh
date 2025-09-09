echo "Start Install Drake"
sudo apt-get update
sudo apt-get install --no-install-recommends \
  ca-certificates gnupg lsb-release wget
wget -qO- https://drake-apt.csail.mit.edu/drake.asc | gpg --dearmor - \
  | sudo tee /etc/apt/trusted.gpg.d/drake.gpg >/dev/null
echo "deb [arch=amd64] https://drake-apt.csail.mit.edu/$(lsb_release -cs) $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/drake.list >/dev/null
sudo apt-get update
sudo apt-get install --no-install-recommends drake-dev
export PATH="/opt/drake/bin${PATH:+:${PATH}}"
export PYTHONPATH="/opt/drake/lib/python$(python3 -c 'import sys; print("{0}.{1}".format(*sys.version_info))')/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

