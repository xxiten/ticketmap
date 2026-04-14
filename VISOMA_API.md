# Visoma API Documentation

Extracted from the Ticketmap project. Documents the subset of the Visoma REST API
that is actively used, intended as a reference for other projects integrating with
the same API.

---

## Overview

| Property | Value |
|---|---|
| **Base URL** | `https://<your-visoma-host>` (e.g. `https://tickets.example.com:10443`) |
| **API version prefix** | `/api2/` |
| **Authentication** | Token – passed as a query parameter |
| **Data format** | JSON |

---

## Authentication

All requests must include a `token` query parameter.

```
GET /api2/...?token=<YOUR_API_TOKEN>
```

The token is a static API key issued by the Visoma instance. There is no session
management or OAuth flow; simply append it to every request.

---

## Endpoints

### Search Tickets

Retrieves a filtered list of tickets.

```
GET /api2/Ticket/search/
```

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | API authentication token |
| `params[typeId]` | integer | no | Filter by ticket type ID. Known value: `6` |
| `params[status]` | integer | no | Filter by ticket status. Known value: `1` (open tickets) |

URL-encoded example:

```
GET /api2/Ticket/search/?token=YOUR_API_TOKEN&params%5BtypeId%5D=6&params%5Bstatus%5D=1
```

Decoded equivalent (for readability):

```
GET /api2/Ticket/search/?token=YOUR_API_TOKEN&params[typeId]=6&params[status]=1
```

#### Response

HTTP `200 OK` with a JSON array of ticket objects.

```json
[
  {
    "Id": "12345",
    "Address": "Main Street 42, 39030 Wengen",
    "CustomerName": "John Doe",
    "Status": "offen",
    "Created": "2024-03-10 14:22:00",
    "Title": "Repair broken window"
  },
  {
    "Id": "12346",
    "Address": "Sample Road 7, 39100 Bozen",
    "CustomerName": "Jane Smith",
    "Status": "in bearbeitung",
    "Created": "2024-04-01 09:05:00",
    "Title": "Cable replacement"
  }
]
```

#### Ticket Object Fields

| Field | Type | Description |
|---|---|---|
| `Id` | string | Unique ticket identifier |
| `Address` | string | Full postal address of the ticket location |
| `CustomerName` | string | Name of the customer / client |
| `Status` | string | Current status label (see [Status Values](#status-values) below) |
| `Created` | string | Creation timestamp in `YYYY-MM-DD HH:MM:SS` format |
| `Title` | string | Short description / title of the ticket |

---

## Status Values

The `Status` field is a human-readable string. The values observed in practice are
German-language labels. Normalize them by lowercasing and stripping whitespace before
comparison.

| Raw value (examples) | Normalized | Meaning |
|---|---|---|
| `"offen"`, `"Offen"` | `offen` | Ticket is open / not yet started |
| `"in bearbeitung"`, `"In Bearbeitung"` | `in bearbeitung` | Work in progress |
| `"erledigt"`, `"Erledigt"` | `erledigt` | Ticket is completed / closed |

Normalization rules used in Ticketmap:

1. Lowercase the entire string.
2. Strip whitespace.
3. Match by substring: `"erledigt"` → done; `"bearbeitung"` → in progress; `"offen"` → open.

---

## Date Format

The `Created` field uses the format `YYYY-MM-DD HH:MM:SS` (e.g. `"2024-03-10 14:22:00"`).
A fallback format of `YYYY-MM-DD` (date only) has also been observed.

Recommended parsing order:

1. `%Y-%m-%d %H:%M:%S`
2. `%Y-%m-%d`
3. Slice to first 10 characters and retry with `%Y-%m-%d`

---

## Ticket URL

Individual tickets can be linked to directly in the Visoma web UI:

```
{base_url}/?id={ticket_id}
```

Example: `https://tickets.example.com:10443/?id=12345`

---

## Error Handling

The API uses standard HTTP status codes. The client should handle at least the
following cases:

| Scenario | Recommended action |
|---|---|
| HTTP 4xx / 5xx | Log the status code and abort; do not retry immediately |
| Connection timeout | Apply a 60-second timeout; treat as a transient failure |
| Malformed JSON | Log and treat response as empty |

---

## Configuration Reference

When integrating this API, the following values need to be configured per deployment:

| Config key | Description | Example |
|---|---|---|
| `ticket_base_url` | Base URL of the Visoma instance | `https://tickets.example.com:10443` |
| `api_token` | Static API token | *(keep secret – do not commit)* |

Environment variable override supported in Ticketmap:
`TICKETMAP_API_TOKEN` takes precedence over any file-based token.

---

## Known Limitations

- Only one endpoint (`/api2/Ticket/search/`) has been explored. Other resources
  likely exist under `/api2/` but are undocumented here.
- The filter parameters (`typeId`, `status`) accept integer codes whose full set of
  valid values is unknown. Values `typeId=6` and `status=1` select open field
  service tickets in the Netixx deployment.
- No pagination mechanism has been observed; the API appears to return all matching
  tickets in a single response.
- HTTPS with a non-standard port (`10443`) is typical for Visoma deployments.
