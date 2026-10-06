// Zoho CRM > Setup > Customization > Modules > Accounts > Buttons > New Button
// Placement: Detail page. Action type: Client Script. Paste this as the button's script.
//
// Sends the engine this payload (via the Deluge function dv_trigger_verification):
// { account_id, customer_number, reason, document_status, attachment_count,
//   requested_by: {id, email}, requested_at, source: "crm_button" }

var accountId = $Page.record_id;

function manilaIsoNow() {
  // e.g. 2026-10-05T15:36:00+08:00
  var d = new Date(Date.now() + 8 * 3600 * 1000);
  return d.toISOString().slice(0, 19) + "+08:00";
}

function outputOf(resp) {
  // Functions.execute wraps the Deluge return value. Log once to confirm the shape in your org.
  return JSON.parse(resp.details.output);
}

ZDK.Client.showLoader({ type: "page", message: "Checking documents..." });
var check = outputOf(ZDK.Apps.CRM.Functions.execute("dv_check_documents", { account_id: accountId }));
ZDK.Client.hideLoader();

if (check.attachment_count === 0) {
  ZDK.Client.showAlert("Please upload at least one document in Attachments before requesting verification.");
} else {
  var input = ZDK.Client.getInput(
    [{ type: "text", label: "Reason (optional)" }],
    "Request Account Verification", "Submit", "Cancel"
  );

  if (input !== false && input !== undefined && input !== null) {
    var reason = (input && input[0]) ? String(input[0]).trim() : "";
    var user = ZDK.Apps.CRM.Users.fetchById($Crm.user.id);

    ZDK.Client.showLoader({ type: "page", message: "Starting verification..." });
    var result = outputOf(ZDK.Apps.CRM.Functions.execute("dv_trigger_verification", {
      account_id: accountId,
      reason: reason,
      attachment_count: check.attachment_count,
      user_id: String($Crm.user.id),
      user_email: user.email,
      requested_at: manilaIsoNow()
    }));
    ZDK.Client.hideLoader();
    ZDK.Client.showAlert(result.message);
  }
}
