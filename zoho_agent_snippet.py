
    def update_lead_context(self, lead):
        """
        Analyzes full lead context (Desc, Notes, Emails) using LLM to update:
        - Last_Conversation (Summary)
        - Last_Conversation_Date
        - Next_Action
        - Next_Action_Date
        """
        print(f"🔄 Analyzing context for {lead.get('Last_Name')}...")
        lead_id = lead.get("id")
        
        # 1. Fetch ALL context
        notes = self.zoho.get_notes(lead_id)
        emails = self.zoho.get_emails(lead_id)
        
        # Format Notes
        notes_text = "No notes."
        if notes:
            notes_text = "\n".join([f"- [{n.get('Created_Time')[:10]}] {n.get('Note_Content')}" for n in notes[:5]])

        # Format Emails
        emails_text = "No emails."
        latest_interaction_date = None
        
        if emails:
            emails.sort(key=lambda x: x.get('sent_time', ''), reverse=True)
            emails_text_list = []
            for e in emails[:5]:
                direction = "SENT" if e.get('sent', True) else "RECEIVED"
                time = e.get('sent_time', '')[:10]
                subject = e.get('subject', 'No Subject')
                snippet = e.get('summary') or "No content"
                emails_text_list.append(f"- [{time}] {direction}: {subject} - {snippet[:150]}...")
                
                if not latest_interaction_date or time > latest_interaction_date:
                    latest_interaction_date = time
            emails_text = "\n".join(emails_text_list)

        # Current Description
        desc = f"{lead.get('Description') or ''}\n{lead.get('Project_Description') or ''}".strip() or "No description."

        # 2. LLM Analysis
        prompt = f"""
        Analyze this sales lead context and update status fields.
        
        LEAD: {lead.get('First_Name')} {lead.get('Last_Name')} ({lead.get('Company')})
        DESCRIPTION: {desc}
        
        RECENT NOTES:
        {notes_text}
        
        RECENT EMAILS:
        {emails_text}
        
        TASK:
        Based ONLY on the recent notes and emails:
        1. Summarize the LAST interaction (email or note) into a short sentence.
        2. Determine the date of that last interaction (YYYY-MM-DD).
        3. Propose the NEXT logical action (e.g., "Follow up on proposal", "Call to discuss requirements") based on the context.
        4. Propose a date for that next action (YYYY-MM-DD). Use your judgment (e.g., 2 days after last email, or tomorrow if urgent).
        
        OUTPUT JSON ONLY:
        {{
            "last_conversation_summary": "...",
            "last_conversation_date": "YYYY-MM-DD",
            "next_action": "...",
            "next_action_date": "YYYY-MM-DD"
        }}
        """
        
        try:
            message = self.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=200,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse JSON
            content = message.content[0].text
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                updates = json.loads(json_match.group(0))
                
                # Validation
                zoho_update = {}
                if updates.get("last_conversation_summary"):
                    zoho_update["Last_Conversation"] = updates["last_conversation_summary"]
                if updates.get("last_conversation_date"):
                    # Format for Zoho: YYYY-MM-DDT09:00:00+05:30
                    zoho_update["Last_Conversation_Date"] = f"{updates['last_conversation_date']}T09:00:00+05:30"
                
                if updates.get("next_action"):
                    zoho_update["Next_Action"] = updates["next_action"]
                if updates.get("next_action_date"):
                    zoho_update["Next_Action_Date"] = f"{updates['next_action_date']}T09:00:00+05:30"

                print(f"✅ Calculated Updates for {lead.get('Last_Name')}: {zoho_update}")
                
                # 3. Push to Zoho
                if zoho_update:
                    self.zoho.update_lead(lead_id, zoho_update)
                    return True, zoho_update
                    
            return False, "Failed to parse AI response"
            
        except Exception as e:
            print(f"Error in update_lead_context: {e}")
            return False, str(e)
