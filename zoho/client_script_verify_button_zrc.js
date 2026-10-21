// Verify Account button (Client Script, Detail page).
// Calls the function dv_verify_account through its REST URL with the values in the URL
// (ZDK Functions.execute drops the arguments in this org).

var accountId = "";
try { accountId = $Page.record_id; } catch (e) {}

var input = ZDK.Client.getInput(
  [{ type: "text", label: "Reason (optional)" }],
  "Verify Account Documents", "Verify", "Cancel"
);

if (!accountId) {
  ZDK.Client.showAlert("Could not read this Account id. Please reload the page and try again.");
} else if (input !== false && input !== undefined && input !== null) {
  var reason = (input && input[0]) ? String(input[0]).trim() : "";

  var userEmail = "";
  try {
    var user = ZDK.Apps.CRM.Users.fetchById($Crm.user.id);
    userEmail = (user && user.email) ? user.email : "";
  } catch (e) {
    userEmail = "";
  }

  var args = {
    account_id: String(accountId),
    reason: reason,
    user_id: String($Crm.user.id),
    user_email: userEmail
  };
  var url = "/crm/v8/functions/dv_verify_account/actions/execute?auth_type=oauth&arguments=" +
            encodeURIComponent(JSON.stringify(args));

  // ZRC is the Zoho Request Client; its name is lower or upper case depending on the version.
  var client = (typeof zrc !== "undefined") ? zrc : ((typeof ZRC !== "undefined") ? ZRC : null);

  if (!client) {
    ZDK.Client.showAlert("ZRC (Zoho Request Client) is not available in this Client Script.");
  } else {
    ZDK.Client.showLoader({ type: "page", message: "Checking documents and starting verification..." });
    client.post(url).then(function (resp) {
      ZDK.Client.hideLoader();
      var body = (resp && resp.data) ? resp.data : resp;
      var text = "";
      try {
        var out = body.details.output;
        var parsed = (typeof out === "string") ? JSON.parse(out) : out;
        text = parsed.message;
      } catch (e) {
        text = "Response: " + JSON.stringify(body).substring(0, 400);
      }
      ZDK.Client.showAlert(text);
    }).catch(function (err) {
      ZDK.Client.hideLoader();
      var detail = "";
      try { detail = JSON.stringify(err); } catch (e) { detail = String(err); }
      ZDK.Client.showAlert("Request failed: " + detail.substring(0, 400));
    });
  }
}
