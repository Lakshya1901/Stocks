# AWS Cloud Deployment Guide (EC2 Free Tier)

Since you need a Static IP for the Groww API whitelist, AWS is a great option. Amazon gives you a free EC2 micro instance and a free Static IP (Elastic IP) for your first 12 months.

Follow these steps exactly to deploy your bot so it runs 24/7.

## Step 1: Create the Server (EC2)

1. Go to [aws.amazon.com](https://aws.amazon.com/) and create a free account if you haven't already.
2. In the AWS Management Console, search for **EC2** and go to the EC2 Dashboard.
3. Click **Launch Instance** (the big orange button).
4. **Name**: `DayTraderBot`
5. **OS / AMI**: Select **Ubuntu** (Ubuntu Server 24.04 LTS or 22.04 LTS is fine) — make sure it says "Free tier eligible".
6. **Instance Type**: `t2.micro` or `t3.micro` (whichever says "Free tier eligible").
7. **Key Pair**: 
   - Select an existing key pair if you have one, or click "Create new key pair".
   - If creating new: leave RSA and `.pem` selected, and click Create. **Keep this file safe** (e.g., in your Downloads folder) — it's your password to the server.
8. **Network Settings**:
   - Check "Allow SSH traffic from Anywhere" (so you can log in).
   - Check "Allow HTTP/HTTPS traffic".
9. Click **Launch Instance**.

## Step 2: Get a Static IP (Elastic IP)

Groww requires your IP to never change. By default, EC2 IPs change when the server reboots. We will lock it with an Elastic IP.

1. On the left sidebar of the EC2 dashboard, scroll down to **Network & Security** and click **Elastic IPs**.
2. Click **Allocate Elastic IP address** (orange button) and click **Allocate**.
3. You will see a new IP address in the list. Select it.
4. Click **Actions** -> **Associate Elastic IP address**.
5. Under "Instance", click the search box and select your `DayTraderBot` instance.
6. Click **Associate**.

**IMPORTANT**: Copy this new Elastic IP address! This is the IP you must submit to Groww for API whitelisting.

## Step 3: Connect to the Server

Open the terminal on your Mac and type these commands. (Replace `123.45.67.89` with your new Elastic IP).

First, fix the permissions on the key file you downloaded (replace `your-key.pem` with your actual key name):
```bash
chmod 400 ~/Downloads/your-key.pem
```

Now connect to the server:
```bash
ssh -i ~/Downloads/your-key.pem ubuntu@123.45.67.89
```
Type `yes` when asked if you want to continue connecting.

## Step 4: Install Dependencies on the Server

Once you see the `ubuntu@...` prompt, run these commands one by one to install Python and Git:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install python3-pip python3-venv git -y
```

## Step 5: Download and Set Up the Bot

1. Clone your code to the server. Since your code is currently on your Mac, the easiest way is to push it to a private GitHub repo, then clone it here:
```bash
git clone https://github.com/yourusername/Stocks.git
cd Stocks
```

2. Create a virtual environment and install the packages:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Set up your `.env` file on the server:
```bash
nano .env
```
Paste your two GROWW_TOTP variables in there. Press `Ctrl+O` then `Enter` to save, and `Ctrl+X` to exit.

## Step 6: Make the Dashboard Accessible

To view the dashboard from your Mac, we need to open port 8080 on AWS:
1. Go back to the AWS EC2 Console.
2. Click on your `DayTraderBot` instance, then click the **Security** tab at the bottom.
3. Click the Security Group link (it looks like `sg-0abc123...`).
4. Click **Edit inbound rules**.
5. Click **Add rule**:
   - Type: Custom TCP
   - Port range: 8080
   - Source: Anywhere-IPv4 (`0.0.0.0/0`)
6. Save rules.

## Step 7: Run it Forever (Systemd Service)

We don't want the bot to die when you close your Mac. We will make it a background service.

1. Create a service file:
```bash
sudo nano /etc/systemd/system/trader.service
```

2. Paste this exact text into it:
```ini
[Unit]
Description=Day Trader Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Stocks
Environment="PATH=/home/ubuntu/Stocks/venv/bin"
ExecStart=/home/ubuntu/Stocks/venv/bin/python -m src.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

3. Save (`Ctrl+O`, `Enter`, `Ctrl+X`).

4. Start the bot and enable it to start on boot:
```bash
sudo systemctl daemon-reload
sudo systemctl enable trader
sudo systemctl start trader
```

## Step 8: Monitor the Bot

The bot is now trading automatically! 

- **View the Dashboard**: Open your browser and go to `http://YOUR_ELASTIC_IP:8080`
- **View Live Logs**: Run `sudo journalctl -u trader -f` in your server terminal.
- **Stop the Bot**: `sudo systemctl stop trader`
- **Restart the Bot**: `sudo systemctl restart trader`

*Note: Don't forget to change `trading.mode: live` in your `config.yaml` and restart the service once Groww approves your static IP!*

## Step 9: Updating Code / Configuration

When you make changes to the code or the config file on your Mac and want to deploy those updates to your server, follow this 3-step workflow:

**1. Push updates from your Mac to GitHub**
Open a terminal on your Mac, go into the `Stocks` folder, and run:
```bash
git add .
git commit -m "Updated trading bot"
git push origin main
```

**2. Pull the updates onto your AWS Server**
Open your AWS server terminal (via EC2 Instance Connect), go into the `Stocks` folder, and download the latest code:
```bash
cd ~/Stocks
git pull origin main
```

**3. Update Dependencies**
If you added new packages to `requirements.txt`, install them:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

**4. Restart the Bot**
Tell the background service to restart so it loads your new code/config:
```bash
sudo systemctl restart trader
```
Your bot will instantly start running on the newest version of the code.
