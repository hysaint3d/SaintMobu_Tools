# MobuWebTracker

A zero-install browser-based tracking tool for MotionBuilder.

## Setup Instructions

1.  **Start the Bridge**:
    - Run `start_MobuVMC_bridge.bat`.
    - It will automatically install `websockets` if missing.
    - Keep this window open. It will relay data from your browser to MotionBuilder.

2.  **Open the Tracker**:
    - Open `MobuFacial_WebTracker.html` in **Google Chrome** or **Microsoft Edge**.
    - Wait for "MediaPipe: Ready" (green status).
    - Click **Start Camera & Tracking**.

3.  **Setup MotionBuilder**:
    - Run the `VMC2Mobu_MultiActor.py` script.
    - Set the receiver port to `39539` (default VMC).
    - You should see the Blendshapes and Hand data arriving!

## Troubleshooting
- **No data in MoBu**: Check if the Bridge console says "Browser connected!". If not, refresh the browser page.
- **Firewall**: Ensure port 8080 (WS) and 39539 (UDP) are allowed on your local network if using separate devices.
- **HTTPS**: If running from a remote server, Chrome requires HTTPS for camera access. For local files (`file:///`), it works fine.
