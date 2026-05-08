@echo off
title MobuOSC_Bridge
echo Starting MobuOSC_Bridge...
echo Target: 127.0.0.1:9000 (Generic OSC / VRChat)
python MobuOSC_bridge.py --target_port 9000
pause
