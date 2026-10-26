# Free server on Oracle Cloud ("Always Free") for the engine

Result: the engine runs 24/7 at `https://dv-dev.rareglobalfood.com` (sandbox) and later
`https://dv.rareglobalfood.com` (production), so the Verify Account button calls it directly.

## 1. Account (15 min)
1. Sign up at oracle.com/cloud/free. A card is asked for identity verification only.
2. **Home region: Singapore** (cannot be changed later).
3. After sign-up: **Billing > Upgrade to Pay As You Go**. Always Free resources stay free, but
   free-tier-only accounts can have idle servers reclaimed (very low CPU for 7 days).
   Set a **budget alert** (Billing > Budgets, e.g. USD 1) so any charge is noticed at once.

## 2. Server (15 min)
Compute > Instances > Create instance:
- Image: **Canonical Ubuntu 24.04** (the aarch64 / ARM one is offered for this shape)
- Shape: **Ampere > VM.Standard.A1.Flex**, **2 OCPU, 12 GB RAM** (free limit: 4 OCPU / 24 GB)
- Networking: create a new VCN with a **public subnet**, **assign a public IPv4 address**
- SSH keys: **Generate a key pair** and **download the private key** (keep it safe)
- Boot volume: 100 GB (free limit 200 GB in total)
If you see **"Out of capacity"**: try another Availability Domain, or retry later.

Note the **Public IP address** on the instance page.

## 3. Open ports 80 and 443 (two places!)
a) Oracle network: Instance > Subnet > **Security List > Add Ingress Rules**:
   source `0.0.0.0/0`, TCP, destination port **80**; same for **443**. (22 is already open.)
b) Ubuntu's own firewall (Oracle images block everything except SSH). After step 4, on the server:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Connect (from the Windows PC)
```cmd
ssh -i "C:\path\to\ssh-key.key" ubuntu@<public IP>
```
(The user is `ubuntu`. If Windows complains about key permissions: right-click the key file >
Properties > Security > Advanced > disable inheritance, keep only your own user.)

## 5. DNS (whoever manages rareglobalfood.com at GoDaddy)
Add record: **Type A, Name `dv-dev`, Value `<public IP>`, TTL 1 hour**.
Check from the PC: `nslookup dv-dev.rareglobalfood.com` shows the IP.

## 6. Install the engine (on the server)
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu && exit        # log in again (ssh ...) for the group to apply
sudo git clone https://github.com/sayprincekumar20/doc-verification.git /opt/doc-verification
sudo chown -R ubuntu:ubuntu /opt/doc-verification
```
From the PC, copy the sandbox settings:
```cmd
scp -i "C:\path\to\ssh-key.key" .env.sandbox ubuntu@<public IP>:/opt/doc-verification/.env
```
On the server, `nano /opt/doc-verification/.env` and set:
```dotenv
APP_ENV=prod
DOMAIN=dv-dev.rareglobalfood.com
POSTGRES_PASSWORD=<long random password>
DATABASE_URL=postgresql+psycopg://dv:<same password>@postgres:5432/dv
KEEP_DOCUMENT_FILES=false
APPLY_APPROVED_REVIEWS=false
REQUEST_POLLING=false
```
Start (the first build takes 10-15 min on ARM):
```bash
cd /opt/doc-verification
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
curl https://dv-dev.rareglobalfood.com/health/live        # {"status":"ok"}
```

## 7. Zoho (sandbox)
- Variables: `dv_engine_url` = `https://dv-dev.rareglobalfood.com`
- Trusted Domain: add `https://dv-dev.rareglobalfood.com`
- Connection `dv_engine`: API key = `API_KEY` in the server's `.env`
- `dv_verify_account`: the **push** version (`zoho/deluge_dv_verify_account.dg`, with invokeurl)

Then: dex child > Verify Account > "Verification started for N document(s)".

## 8. Keep it running
- Daily backup: `crontab -e` > `15 2 * * * /opt/doc-verification/deploy/backup.sh`
- Updates: `cd /opt/doc-verification && git pull && docker compose -f docker-compose.prod.yml up -d --build`
- Logs: `docker compose -f docker-compose.prod.yml logs -f worker`
