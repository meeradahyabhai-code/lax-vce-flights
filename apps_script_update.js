/**
 * Google Apps Script — Add this to the existing doGet function.
 *
 * SETUP REQUIRED:
 * 1. Open the Google Sheet linked to your Apps Script
 * 2. Create two new tabs: "Arriving Venice" and "Departing Istanbul"
 * 3. In "Arriving Venice" add headers in row 1:
 *    Traveler | Airline | Flight Number | From (Airport) | From (City) | Departure Date | Departure Time | Arrival Date | Arrival Time | Stopover Airport | Stopover City | Flight ID | Added By | Timestamp
 * 4. In "Departing Istanbul" add headers in row 1:
 *    Traveler | Airline | Flight Number | To (Airport) | To (City) | Departure Date | Departure Time | Arrival Date | Arrival Time | Stopover Airport | Stopover City | Flight ID | Added By | Timestamp
 * 5. Add the new action handlers below to your existing doGet function
 * 6. Re-deploy the Apps Script (Deploy > New deployment)
 */

// ---- Add these cases inside your existing doGet(e) function ----

function handleConfirmedFlights(e) {
  var action = e.parameter.action;
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  if (action === 'add_confirmed_flight') {
    var tabName = e.parameter.tab === 'departing_istanbul' ? 'Departing Istanbul' : 'Arriving Venice';
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return ContentService.createTextOutput(JSON.stringify({error: 'Tab not found'})).setMimeType(ContentService.MimeType.JSON);

    var travelers = (e.parameter.travelers || '').split(',');
    var now = new Date().toISOString();
    var flightId = e.parameter.flight_id || '';

    travelers.forEach(function(traveler) {
      traveler = traveler.trim();
      if (!traveler) return;
      sheet.appendRow([
        traveler,
        e.parameter.airline || '',
        e.parameter.flight_number || '',
        e.parameter.departure_airport || '',
        e.parameter.departure_city || '',
        e.parameter.departure_date || '',
        e.parameter.departure_time || '',
        e.parameter.arrival_date || '',
        e.parameter.arrival_time || '',
        e.parameter.stopover_airport || '',
        e.parameter.stopover_city || '',
        flightId,
        e.parameter.added_by || '',
        now
      ]);
    });

    return ContentService.createTextOutput(JSON.stringify({status: 'ok', flight_id: flightId})).setMimeType(ContentService.MimeType.JSON);
  }

  if (action === 'get_confirmed_flights') {
    var tabName = e.parameter.tab === 'departing_istanbul' ? 'Departing Istanbul' : 'Arriving Venice';
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return ContentService.createTextOutput('[]').setMimeType(ContentService.MimeType.JSON);

    var data = sheet.getDataRange().getDisplayValues();
    return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  }

  if (action === 'delete_confirmed_flight') {
    var tabName = e.parameter.tab === 'departing_istanbul' ? 'Departing Istanbul' : 'Arriving Venice';
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return ContentService.createTextOutput(JSON.stringify({error: 'Tab not found'})).setMimeType(ContentService.MimeType.JSON);

    var flightId = e.parameter.flight_id || '';
    var deletedBy = (e.parameter.deleted_by || '').toLowerCase();
    var data = sheet.getDataRange().getDisplayValues();
    var headers = data[0];
    var fidCol = headers.indexOf('Flight ID');
    var addedByCol = headers.indexOf('Added By');

    // Find rows matching flight_id
    var rowsToDelete = [];
    for (var i = data.length - 1; i >= 1; i--) {
      if (String(data[i][fidCol]) === flightId) {
        var originalAdder = String(data[i][addedByCol] || '').toLowerCase();
        if (originalAdder === deletedBy || deletedBy === '') {
          rowsToDelete.push(i + 1); // 1-indexed
        } else {
          // Send email for approval
          MailApp.sendEmail({
            to: 'mdahya@gmail.com',
            subject: 'Flight deletion request',
            body: deletedBy + ' wants to delete flight ' + flightId + ' which was added by ' + data[i][addedByCol]
          });
          return ContentService.createTextOutput(JSON.stringify({status: 'pending_approval'})).setMimeType(ContentService.MimeType.JSON);
        }
      }
    }

    // Delete rows from bottom up
    rowsToDelete.forEach(function(row) {
      sheet.deleteRow(row);
    });

    return ContentService.createTextOutput(JSON.stringify({status: 'ok'})).setMimeType(ContentService.MimeType.JSON);
  }

  if (action === 'join_flight') {
    var tabName = e.parameter.tab === 'departing_istanbul' ? 'Departing Istanbul' : 'Arriving Venice';
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return ContentService.createTextOutput(JSON.stringify({error: 'Tab not found'})).setMimeType(ContentService.MimeType.JSON);

    var flightId = e.parameter.flight_id || '';
    var traveler = e.parameter.traveler || '';
    var data = sheet.getDataRange().getDisplayValues();
    var headers = data[0];
    var fidCol = headers.indexOf('Flight ID');

    // Find first row with matching flight_id to copy flight data
    var templateRow = null;
    for (var i = 1; i < data.length; i++) {
      if (String(data[i][fidCol]) === flightId) {
        templateRow = data[i].slice();
        break;
      }
    }

    if (templateRow) {
      templateRow[0] = traveler; // Traveler column
      templateRow[headers.indexOf('Added By')] = traveler;
      templateRow[headers.indexOf('Timestamp')] = new Date().toISOString();
      sheet.appendRow(templateRow);
    }

    return ContentService.createTextOutput(JSON.stringify({status: 'ok'})).setMimeType(ContentService.MimeType.JSON);
  }

  return null; // Not a confirmed flight action
}

// ---- In your existing doGet(e), add at the top: ----
// var confirmedResult = handleConfirmedFlights(e);
// if (confirmedResult) return confirmedResult;
