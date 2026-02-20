
from zoho_agent import agent
import requests
import json

def debug_lead(name_query):
    print(f"Searching for lead: {name_query}")
    # Fetch all leads to find ID
    leads = agent.zoho.get_leads()
    if not leads:
        print("No leads returned by get_leads()")
        return
        
    target = None
    for l in leads:
        full_name = f"{l.get('First_Name')} {l.get('Last_Name')}"
        if name_query.lower() in full_name.lower():
            target = l
            break
    
    if not target:
        print(f"Lead '{name_query}' not found in {len(leads)} leads.")
        return

    print(f"Found Lead: {target.get('id')} - {target.get('Full_Name')} ({target.get('Email')})")
    
    lead_id = target.get('id')
    headers = agent.zoho._get_headers()
    
    # 1. Try v3 Emails
    print("\n--- [Test 1] Fetching Emails via v3 ---")
    url_v3 = f"{agent.zoho.api_domain}/crm/v3/Leads/{lead_id}/Emails"
    res_v3 = requests.get(url_v3, headers=headers)
    print(f"Status: {res_v3.status_code}")
    try:
        data = res_v3.json()
        if "data" in data and data["data"]:
            print(f"Found {len(data['data'])} emails.")
            print("Sample:", json.dumps(data["data"][0], indent=2))
        else:
            print("Response JSON:", json.dumps(data, indent=2))
    except:
        print("Response Text:", res_v3.text)

    # 2. Try v2 Emails (just in case)
    print("\n--- [Test 2] Fetching Emails via v2 ---")
    url_v2 = f"{agent.zoho.api_domain}/crm/v2/Leads/{lead_id}/Emails"
    res_v2 = requests.get(url_v2, headers=headers)
    print(f"Status: {res_v2.status_code}")
    try:
        print("Response JSON:", json.dumps(res_v2.json(), indent=2)[:500])
    except:
        print("Response Text:", res_v2.text[:500])

    # 3. Check Related Lists Metadata to find correct API Name
    print("\n--- [Test 3] related_lists Metadata ---")
    url_meta = f"{agent.zoho.api_domain}/crm/v3/settings/related_lists?module=Leads"
    res_meta = requests.get(url_meta, headers=headers)
    if res_meta.status_code == 200:
        rls = res_meta.json().get("related_lists", [])
        email_rl = [r for r in rls if "email" in r.get("api_name", "").lower() or "mail" in r.get("display_label", "").lower()]
        print("Found Email-related lists:", json.dumps(email_rl, indent=2))
    else:
        print(f"Failed to fetch metadata: {res_meta.status_code} {res_meta.text}")

if __name__ == "__main__":
    debug_lead("Rakshak")
