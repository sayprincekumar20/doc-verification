// Zoho CRM > Setup > Customization > Modules and Fields > Accounts > Buttons > + New Button
//   Name: Verify Account   Placement: Detail page (In Record)   Action: Client Script
// Asks for an optional reason, then calls the Deluge function dv_verify_account ONCE:
// it checks for documents and sends the Account, Attachments and Notes to the engine.
// Needs REST API > OAuth 2.0 switched on for dv_verify_account.

var accountId = "";
try { accountId = $Page.record_id; } catch (e) {}
if (!accountId) { try { accountId = $Page.record.id; } catch (e) {} }
if (!accountId && typeof context !== "undefined" && context) {
  accountId = context.record_id || (context.record && context.record.id) || "";
}

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
        if (typeof parsed === "string") {
          var inner = JSON.parse(parsed);
          if (inner && inner.message) { return inner; }
        }
      } catch (e) {
        if (c.indexOf("message") === -1) { return { ok: true, message: c }; }
      }
    }
  }
  var raw = "";
  try { raw = JSON.stringify(resp); } catch (e) { raw = String(resp); }
  console.log("dv_verify_account raw response:", raw);
  return { ok: false, message: "Unexpected response from dv_verify_account: " + raw.substring(0, 400) };
}

var input = ZDK.Client.getInput(
  [{ type: "text", label: "Reason (optional)" }],
  "Verify Account Documents", "Verify", "Cancel"
);

if (!accountId) {
  ZDK.Client.showAlert("Could not read this Account's id. Please reload the page and try again.");
} else if (input !== false && input !== undefined && input !== null) {
  var reason = (input && input[0]) ? String(input[0]).trim() : "";

  var userEmail = "";
  try {
    var user = ZDK.Apps.CRM.Users.fetchById($Crm.user.id);
    userEmail = (user && user.email) ? user.email : "";
  } catch (e) {
    userEmail = "";   // not essential: the verification still runs
  }

  var args = {
    account_id: String(accountId),
    reason: reason,
    user_id: String($Crm.user.id),
    user_email: userEmail
  };
  // How Zoho passes arguments to a REST-enabled function from a Client Script is not clearly
  // documented: try the known formats in order; a wrong one returns "No account id received"
  // at once (no job started). The alert says which format worked.
  var formats = [
    { name: "object", payload: args },
    { name: "arguments-json", payload: { arguments: JSON.stringify(args) } },
    { name: "json-text", payload: JSON.stringify(args) },
    { name: "arguments-json-text", payload: JSON.stringify({ arguments: JSON.stringify(args) }) }
  ];
  var result = { ok: false, message: "No response." };
  var used = "";
  ZDK.Client.showLoader({ type: "page", message: "Checking documents and starting verification..." });
  try {
    for (var f = 0; f < formats.length; f++) {
      used = formats[f].name;
      try {
        result = outputOf(ZDK.Apps.CRM.Functions.execute("dv_verify_account", formats[f].payload));
      } catch (err) {
        result = { ok: false, message: "No account id received (" + used + " failed: " +
                   (err && err.message ? err.message : err) + ")" };
      }
      var msg = String(result.message || "");
      if (msg.indexOf("No account id received") === -1 && msg.indexOf("Function received") === -1) {
        break;   // the function got the account id with this format
      }
    }
  } catch (e) {
    result = { ok: false, message: "Could not start the verification: " + (e && e.message ? e.message : e) };
  } finally {
    ZDK.Client.hideLoader();
  }
  result.message = result.message + "  [format: " + used + "]";
  ZDK.Client.showAlert(result.message);
}
