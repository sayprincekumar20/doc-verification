# Production Zoho fields used by the engine (export 2026-10-29)

Source: `python scripts/list_fields.py --env .env.production` (Accounts 171 fields, Contacts 67).

## Accounts
| Engine use | API name | Type | Production notes |
| --- | --- | --- | --- |
| COD gate / activation | Credit_Terms (Payment Terms) | picklist | **"Cash On Delivery" is not in the picklist definition** (records still carry it) - check Setup |
| Activation result | Customer_Status | picklist | Active, Inactive |
| TIN check / update | Tax_Identification_Number_TIN | text | |
| Invoice name check / update | Invoice_Company_Name | text | |
| Address check | Billing_Street (+ Billing_City, Billing_Province or Billing_State, Billing_Code) | text / picklist | Billing_Province picklist has "Metro Manila" (older records: "National Capital Region (Manila)"); both = NCR |
| Owner update | Owner_Name | text | |
| Business form | Type_of_Business_Organization | picklist | Single Proprietorship, Partnership, Parnership, Other - **no "Corporation"** |
| Business style | Business_Style | text | |
| Email / phone | Email (email), Phone (phone) | | |
| Not used | Contact_Person | text | label "Bank Contact Person" |
| Customer number | Inventory_Customer_Number | text | |
| Branches | Account_Level (Parent, Branch, Standalone, Sub-Parent), Parent_Account (lookup) | | |
| Document files | BIR_Registration_COR, Business_Permit, Business_registration, General_Information_Sheet | fileupload | |

## Contacts
| Engine use | API name | Type | Production notes |
| --- | --- | --- | --- |
| Link to the Account | Account_Name | lookup -> Accounts | |
| Accounting contact | Contact_of | multi-select | values: Purchasing, Sales, **Accouting** (typo), Human Resource, Information Technology, Supply Chain - the engine accepts both spellings |
| Primary contact | Account_Primary_Contact | boolean | (sandbox has Primary_Contact instead) |
| Name / email | Full_Name, Email | | |
| Mobile | Mobile, else Phone | phone | Phone is labelled "Secondary Mobile" |
| Also read | Position_Role (Accounting Manager / Staff...), Department, Contact_Category, Contact_Type | | |
