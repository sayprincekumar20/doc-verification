// Zoho CRM > Setup > Customization > Modules and Fields > Accounts > Buttons > + New Button
//   Name: Verify Account   Placement: Detail page (In Record)   Action: Client Script
// Paste this script. It asks for an optional reason, then calls the Deluge function
// dv_verify_account ONCE: that function checks for documents and sends the Account,
// Attachments and Notes to the verification engine in a single request.

var accountId = $Page.record_id;

function outputOf(resp) {
  // Functions.execute wraps the Deluge return value in details.output (a JSON string).
  try {
    return JSON.parse(resp.details.output);
  } catch (e) {
    return { ok: false, message: "Unexpected response from dv_verify_account. Please contact the CRM admin." };
  }
}

var input = ZDK.Client.getInput(
  [{ type: "text", label: "Reason (optional)" }],
  "Verify Account Documents", "Verify", "Cancel"
);

if (input !== false && input !== undefined && input !== null) {
  var reason = (input && input[0]) ? String(input[0]).trim() : "";
  var user = ZDK.Apps.CRM.Users.fetchById($Crm.user.id);

  ZDK.Client.showLoader({ type: "page", message: "Checking documents and starting verification..." });
  var result = outputOf(ZDK.Apps.CRM.Functions.execute("dv_verify_account", {
    account_id: String(accountId),
    reason: reason,
    user_id: String($Crm.user.id),
    user_email: user ? user.email : ""
  }));
  ZDK.Client.hideLoader();
  ZDK.Client.showAlert(result.message);
}
