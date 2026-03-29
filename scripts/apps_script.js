function doGet(e) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const action = e.parameter.action || '';

  // ── ADD CONFIRMED FLIGHT ──────────────────────────────
  if (action === 'add_confirmed_flight') {
    const tabName = e.parameter.tab === 'departing_istanbul'
      ? 'Departing Istanbul'
      : 'Arriving Venice';
    const sheet = ss.getSheetByName(tabName);
    if (!sheet) {
      return json({status: 'error', message: 'Tab not found: ' + tabName});
    }
    const travelers = (e.parameter.travelers || '')
      .split(',').map(t => t.trim()).filter(t => t);
    if (travelers.length === 0) {
      return json({status: 'error', message: 'No travelers provided'});
    }
    const flightId = e.parameter.flight_id || '';
    const now = new Date();
    travelers.forEach(traveler => {
      sheet.appendRow([
        traveler,
        e.parameter.airline || '',
        e.parameter.flight_number || '',
        e.parameter.departure_airport || e.parameter.from_airport || '',
        e.parameter.departure_city || e.parameter.from_city || '',
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
    return json({status: 'ok', flight_id: flightId});
  }

  // ── GET CONFIRMED FLIGHTS ─────────────────────────────
  if (action === 'get_confirmed_flights') {
    const tabName = e.parameter.tab === 'departing_istanbul'
      ? 'Departing Istanbul'
      : 'Arriving Venice';
    const sheet = ss.getSheetByName(tabName);
    if (!sheet) {
      return json({status: 'error', message: 'Tab not found: ' + tabName});
    }
    const rows = sheet.getDataRange().getDisplayValues();
    const headers = rows[0];
    const data = rows.slice(1)
      .filter(row => row[0] && row[0].toString().trim() !== '')
      .map(row => {
        const obj = {};
        headers.forEach((h, i) => obj[h] = row[i]);
        return obj;
      });
    return json({status: 'ok', flights: data});
  }

  // ── DELETE CONFIRMED FLIGHT ───────────────────────────
  if (action === 'delete_confirmed_flight') {
    const flightId = e.parameter.flight_id || '';
    const deletedBy = e.parameter.deleted_by || '';
    const tabs = ['Arriving Venice', 'Departing Istanbul'];
    let deleted = false;
    let addedBy = '';

    tabs.forEach(tabName => {
      const sheet = ss.getSheetByName(tabName);
      if (!sheet) return;
      const rows = sheet.getDataRange().getDisplayValues();
      for (let i = rows.length - 1; i >= 1; i--) {
        if (rows[i][11] === flightId) {
          addedBy = rows[i][12];
          if (addedBy === deletedBy) {
            sheet.deleteRow(i + 1);
            deleted = true;
          }
        }
      }
    });

    if (deleted) {
      return json({status: 'ok'});
    } else {
      GmailApp.sendEmail(
        'mdahya@gmail.com',
        'Flight deletion request — Dahyabhai Cruise Flights',
        deletedBy + ' has requested to delete flight ' + flightId +
        ', which was added by ' + addedBy +
        '. Log in to your Google Sheet to approve or deny this request.'
      );
      return json({status: 'pending_approval'});
    }
  }

  // ── JOIN FLIGHT ───────────────────────────────────────
  if (action === 'join_flight') {
    const flightId = e.parameter.flight_id || '';
    const traveler = (e.parameter.traveler || '').trim();
    const tabs = ['Arriving Venice', 'Departing Istanbul'];

    for (const tabName of tabs) {
      const sheet = ss.getSheetByName(tabName);
      if (!sheet) continue;
      const rows = sheet.getDataRange().getDisplayValues();
      const matchRow = rows.slice(1).find(r => r[11] === flightId);
      if (matchRow) {
        const newRow = [...matchRow];
        newRow[0] = traveler;
        newRow[12] = traveler;
        newRow[13] = new Date();
        sheet.appendRow(newRow);
        return json({status: 'ok', flight_id: flightId});
      }
    }
    return json({status: 'error', message: 'Flight not found: ' + flightId});
  }

  // ── ADD CONFIRMED HOTEL ───────────────────────────────
  if (action === 'add_confirmed_hotel') {
    const sheet = ss.getSheetByName('Confirmed Hotels');
    if (!sheet) {
      return json({status: 'error', message: 'Tab "Confirmed Hotels" not found. Run createConfirmedHotelTab() first.'});
    }
    const now = new Date();
    sheet.appendRow([
      e.parameter.traveler || '',
      e.parameter.hotel_name || '',
      e.parameter.address || '',
      e.parameter.city || '',
      e.parameter.check_in || '',
      e.parameter.check_out || '',
      e.parameter.hotel_id || '',
      e.parameter.added_by || '',
      e.parameter.source || 'manual',
      now
    ]);
    return json({status: 'ok', hotel_id: e.parameter.hotel_id || ''});
  }

  // ── GET CONFIRMED HOTELS ──────────────────────────────
  if (action === 'get_confirmed_hotels') {
    const sheet = ss.getSheetByName('Confirmed Hotels');
    if (!sheet) {
      return json([]);
    }
    const rows = sheet.getDataRange().getDisplayValues();
    if (rows.length <= 1) return json([]);
    const headers = rows[0];
    const data = rows.slice(1)
      .filter(row => row[0] && row[0].toString().trim() !== '')
      .map(row => {
        const obj = {};
        headers.forEach((h, i) => {
          // Normalize header to snake_case keys the frontend expects
          const key = h.toLowerCase().replace(/\s+/g, '_');
          obj[key] = row[i];
        });
        return obj;
      });
    return json(data);
  }

  // ── JOIN HOTEL ────────────────────────────────────────
  if (action === 'join_hotel') {
    const hotelId = e.parameter.hotel_id || '';
    const traveler = (e.parameter.traveler || '').trim();
    const sheet = ss.getSheetByName('Confirmed Hotels');
    if (!sheet) {
      return json({status: 'error', message: 'Tab "Confirmed Hotels" not found'});
    }
    const rows = sheet.getDataRange().getDisplayValues();
    const headers = rows[0];
    const hidCol = headers.indexOf('Hotel ID');
    const matchRow = rows.slice(1).find(r => r[hidCol] === hotelId);
    if (matchRow) {
      const newRow = [...matchRow];
      newRow[0] = traveler;                // Traveler
      newRow[7] = traveler;                // Added By
      newRow[8] = 'joined';               // Source
      newRow[9] = new Date();             // Timestamp
      sheet.appendRow(newRow);
      return json({status: 'ok', hotel_id: hotelId});
    }
    return json({status: 'error', message: 'Hotel not found: ' + hotelId});
  }

  // ── DELETE HOTEL ──────────────────────────────────────
  if (action === 'delete_hotel') {
    const hotelId = e.parameter.hotel_id || '';
    const traveler = (e.parameter.traveler || '').trim();
    const sheet = ss.getSheetByName('Confirmed Hotels');
    if (!sheet) {
      return json({status: 'error', message: 'Tab "Confirmed Hotels" not found'});
    }
    const rows = sheet.getDataRange().getDisplayValues();
    const headers = rows[0];
    const hidCol = headers.indexOf('Hotel ID');
    let deleted = false;
    for (let i = rows.length - 1; i >= 1; i--) {
      if (rows[i][hidCol] === hotelId && rows[i][0] === traveler) {
        sheet.deleteRow(i + 1);
        deleted = true;
        break;
      }
    }
    return json({status: deleted ? 'ok' : 'not_found'});
  }

  // ── LOG FAMILY PICK ───────────────────────────────────
  if (e.parameter && e.parameter.name) {
    const sheet = ss.getActiveSheet();
    sheet.appendRow([
      new Date(),
      e.parameter.name || '',
      e.parameter.departure_airport || '',
      e.parameter.action || '',
      e.parameter.airline || '',
      e.parameter.flight_date || '',
      e.parameter.departure_time || '',
      e.parameter.arrival_time || '',
      e.parameter.layover || '',
      e.parameter.price || '',
      e.parameter.fare_type || '',
      e.parameter.flight_id || ''
    ]);
    return json({status: 'ok'});
  }

  // ── EMAIL SCREENSHOT ─────────────────────────────────
  if (action === 'email_screenshot') {
    const travelers = e.parameter.travelers || 'Unknown';
    const airline = e.parameter.airline || 'Unknown airline';
    const route = e.parameter.route || '';
    const dates = e.parameter.dates || '';
    const base64Image = e.parameter.image || '';

    let emailBody = 'New flight uploaded by: ' + travelers + '\n\n';
    emailBody += 'Airline: ' + airline + '\n';
    emailBody += 'Route: ' + route + '\n';
    emailBody += 'Dates: ' + dates + '\n';

    if (base64Image) {
      const decoded = Utilities.base64Decode(base64Image);
      const blob = Utilities.newBlob(decoded, 'image/png', 'booking-confirmation.png');
      GmailApp.sendEmail('mdahya@gmail.com',
        'New flight upload — ' + travelers + ' on ' + airline,
        emailBody,
        { attachments: [blob] }
      );
    } else {
      GmailApp.sendEmail('mdahya@gmail.com',
        'New flight upload — ' + travelers + ' on ' + airline,
        emailBody
      );
    }
    return json({ status: 'ok' });
  }

  // ── UPDATE CONFIRMED FLIGHT ───────────────────────────
  if (action === 'update_confirmed_flight') {
    var tabName = e.parameter.tab === 'departing_istanbul' ? 'Departing Istanbul' : 'Arriving Venice';
    var updateSheet = ss.getSheetByName(tabName);
    if (!updateSheet) return json({error: 'Tab not found'});

    var flightId = e.parameter.flight_id || '';
    var data = updateSheet.getDataRange().getDisplayValues();
    var headers = data[0];
    var fidCol = headers.indexOf('Flight ID');

    for (var i = data.length - 1; i >= 1; i--) {
      if (String(data[i][fidCol]) === flightId) {
        var row = i + 1;
        updateSheet.getRange(row, 2).setValue(e.parameter.airline || '');
        updateSheet.getRange(row, 3).setValue(e.parameter.flight_number || '');
        updateSheet.getRange(row, 4).setValue(e.parameter.departure_airport || '');
        updateSheet.getRange(row, 5).setValue(e.parameter.departure_city || '');
        updateSheet.getRange(row, 6).setValue(e.parameter.departure_date || '');
        updateSheet.getRange(row, 7).setValue(e.parameter.departure_time || '');
        updateSheet.getRange(row, 8).setValue(e.parameter.arrival_date || '');
        updateSheet.getRange(row, 9).setValue(e.parameter.arrival_time || '');
        updateSheet.getRange(row, 10).setValue(e.parameter.stopover_airport || '');
      }
    }
    return json({status: 'ok'});
  }

  // ── READ FAMILY PICKS ─────────────────────────────────
  const sheet = ss.getActiveSheet();
  const rows = sheet.getDataRange().getDisplayValues();
  return ContentService.createTextOutput(JSON.stringify(rows))
    .setMimeType(ContentService.MimeType.JSON);
}

function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function createConfirmedFlightTabs() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const tab1 = ss.getSheetByName('Arriving Venice') || ss.insertSheet('Arriving Venice');
  tab1.getRange(1, 1, 1, 14).setValues([[
    'Traveler', 'Airline', 'Flight Number', 'From (Airport)', 'From (City)',
    'Departure Date', 'Departure Time', 'Arrival Date', 'Arrival Time (VCE)',
    'Stopover Airport', 'Stopover City', 'Flight ID', 'Added By', 'Timestamp'
  ]]);

  const tab2 = ss.getSheetByName('Departing Istanbul') || ss.insertSheet('Departing Istanbul');
  tab2.getRange(1, 1, 1, 14).setValues([[
    'Traveler', 'Airline', 'Flight Number', 'To (Airport)', 'To (City)',
    'Departure Date (IST)', 'Departure Time (IST)', 'Arrival Date', 'Arrival Time',
    'Stopover Airport', 'Stopover City', 'Flight ID', 'Added By', 'Timestamp'
  ]]);
}

function createConfirmedHotelTab() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tab = ss.getSheetByName('Confirmed Hotels') || ss.insertSheet('Confirmed Hotels');
  tab.getRange(1, 1, 1, 10).setValues([[
    'Traveler', 'Hotel Name', 'Address', 'City',
    'Check In', 'Check Out', 'Hotel ID',
    'Added By', 'Source', 'Timestamp'
  ]]);
}
