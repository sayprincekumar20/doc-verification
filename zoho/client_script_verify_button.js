// Zoho CRM > Setup > Customization > Modules and Fields > Accounts > Buttons > + New Button
//   Name: Verify Account   Placement: Detail page (In Record)   Action: Client Script
// Paste this script. It asks for an optional reason, then calls the Deluge function
// dv_verify_account ONCE: that function checks for documents and sends the Account,
// Attachments and Notes to the verification engine in a single request.

var accountId = $Page.record_id;

function outputOf(resp) {
  // Read the Deluge return value, whatever shape Functions.execute returns it in.
  var candidates = [];
  if (resp && resp.details) {
    candidates.push(resp.details.output, resp.details.userMessage, resp.details.output_string);
  }
  if (resp) {
    candidates.push(resp.output, resp.result, resp.data, resp);
  }
  for (var i = 0; i < candidates.length; i++) {
    var c = candidates[i];
    if (c === undefined || c === null || c === "") { continue; }
    if (typeof c === "object" && c.message) { return c; }
    if (typeof c === "string") {
      try {
        var parsed = JSON.parse(c);
        if (parsed && parsed.message) { return parsed; }
        if (typeof parsed === "string") {           // returned as a JSON string inside a string
          var inner = JSON.parse(parsed);
          if (inner && inner.message) { return inner; }
        }
      } catch (e) {
        if (c.indexOf("message") === -1) { return { ok: true, message: c }; }
      }
    }
  }
  // Unknown shape: show it, so it can be fixed.
  var raw = "";
  try { raw = JSON.stringify(resp); } catch (e) { raw = String(resp); }
  console.log("dv_verify_account raw response:", raw);
  return { ok: false, message: "Unexpected response from dv_verify_account: " + raw.substring(0, 400) };
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
