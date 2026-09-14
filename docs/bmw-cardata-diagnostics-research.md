# BMW Vehicle Data, Fault History, and Seat-System Diagnostics

## Executive conclusion

BMW exposes several data products, but no single owner API provides a complete diagnostic history.

- **Best immediate action:** request a historic BMW CarData archive now, covering the full three-week failure and the earlier service visit. BMW says backend retention varies from 30 days to product lifetime, so delay can destroy evidence.[^1]
- **Best fault evidence:** ask BMW service to export a complete ISTA vehicle test before clearing faults or resetting modules. ECU fault memory can contain active, pending, and previously active faults, occurrence counts, timestamps, and snapshots; ordinary CarData may expose only BMW's backend copy.[^2]
- **Best future monitoring:** extend the existing MQTT setup with raw fault memory, 12 V battery voltage/state of charge, Battery Guard, and related status descriptors when this VIN supports them.[^3]
- **Most relevant seat evidence:** BMW's current EU Data Act metadata documents seat-adjustment events, command source including `User Profile Position`, function state, activated constraints, error IDs, affected function, and UTC timestamps.[^4] Availability for this specific VIN and in the owner archive must be tested; these fields are not listed as dedicated customer REST endpoints.
- **Do not start with Connected RMI:** it is a paid business/workshop product requiring service setup and customer-granted vehicle assignment. It is useful if the owner archive and dealer report are insufficient, but current consumer credentials cannot call it.[^5]

The symptom cluster—memory position forgotten, comfort exit inactive, and automatic profile positioning inactive—points to a shared seat/profile/power or communication path. Spontaneous recovery does not prove repair. Plausible causes include intermittent low-voltage state, module sleep/wake or bus communication, user-profile synchronization, software restart, seat-position calibration/constraint, or a seat-module fault. This is diagnostic prioritization, not a diagnosis.

## What each BMW channel can provide

| Channel | Historical data | Fault data | Seat/profile detail | Automation | Access verdict |
|---|---|---|---|---|---|
| Owner CarData archive | Yes; BMW describes latest or historic ZIP archive | Possible, depending on VIN, transmission, package, and retention | Possible through detailed EU Data Act packages, but not guaranteed | Manual download | **Use now** |
| Customer CarData REST API | Latest BMW-backend value only; charging history is a special endpoint | Raw fault memory and Check Control data may be returned if available | No standard seat-memory/profile endpoint found | Yes; 50 requests/day | Useful snapshots, not retrospective investigation |
| Customer CarData MQTT stream | Events transmitted after stream is configured; BMW does not retain a personal replay queue | Raw fault-memory descriptor is marked streamable | Standard catalogue lacks memory-position/profile fields | Yes; one connection per GCID | **Use for future evidence** |
| Third-party CarData Historic API | Date-bounded asynchronous historic-data request | Depends on granted data package | Current EU Data Act package documents detailed seat events | Yes | Business registration, explicit consent, clearance ID, tariff |
| Connected RMI | Service history, repair history, remote diagnostic sessions | DTC status, occurrence count, ECU, timestamp, snapshot; remote jobs can also clear DTCs | ECU-level diagnostics, model dependent | Yes | Workshop/business path; paid and higher risk |
| Local OBD/ISTA at workshop | ECU-local history until overwritten or cleared | Most direct source for current and previously active DTCs and environment data | Best model-specific test plan and calibration view | Local service tool | **Best diagnostic path** |

## Owner portal: what to request

BMW Group says owners can request a CarData report at any time. It contains the most recent essential telematics stored on BMW servers. Current customer documentation also describes a downloadable **latest or historic Data Archive** as a ZIP file.[^6] Portal labels vary by language and release.

Recommended procedure:

1. Sign in to regional My BMW / ConnectedDrive using the account marked as **primary user** for the vehicle.
2. Open vehicle's BMW CarData area: `https://bmw-cardata.bmwgroup.com/customer/public/home`.
3. Request a historic archive for the affected VIN. If date selection is offered, request at least **2026-08-20 through 2026-09-14**, plus a separate range covering the previous service visit.
4. Preserve original ZIP unchanged. Record request time, selected range, VIN, and portal options.
5. Search extracted filenames, table names, and headers for:
   - `diagnosticTroubleCodes`, `fault`, `DTC`, `checkControl`, `ccm`
   - `electricalSystem`, `battery`, `voltage`, `stateOfCharge`, `BatteryGuard`
   - `conditionBasedServices`, `teleservice`
   - `usagecounter_seat`, `position_seat`, `seat_operation_dr`
   - `adj_cmd_source`, `adj_func_state`, `contraint_activated` (BMW metadata spelling), `fusi_event_error_id`, `fusi_event_module_id`, `affected_function`
6. Keep rows from before, during, and after failure. Correlate by UTC timestamp with driver/profile changes, cold starts, long parking, charging, service, and recovery date.

BMW's Romanian Data Act notice makes the retention boundary important: much in-vehicle data is volatile; maintenance, wear, and technical-fault information is stored in control units, but vehicle-stored data is typically deleted during service. BMW backend retention varies by data type.[^1] Therefore:

- Earlier seat-module reset may have removed pre-service ECU evidence.
- Recent three-week failure may still leave a previously-active DTC or event.
- CarData archive may contain a backend copy even if ECU memory was reset, but only if vehicle transmitted it and retention still covers it.

CarData's transaction report is not the same artifact. It records data BMW shared with third parties; it is not complete vehicle fault or repair history.[^6]

## Data available through customer CarData API

Current customer Swagger exposes mappings, containers, telematic data, basic vehicle data, charging history, vehicle image, location-based charging settings, and tyre diagnosis. It exposes no general historic-data endpoint.[^7] The telematic endpoint accepts VIN and container ID and returns most recent stored values for configured descriptors.

Important limits and semantics:

- Primary-user mapping, active ConnectedDrive contract, supported market, and telematics-capable vehicle are required.
- OAuth scopes already used by this repository are correct: `cardata:api:read` and `cardata:streaming:read`.
- REST limit: 50 requests per day, reset at 00:00 UTC; up to 10 containers.[^8]
- Values originate from BMW backend. A request does not wake the car or read an ECU live.
- Each value's timestamp is critical; current API response can be stale.
- MQTT is QoS 0. Consumer must persist events itself; missed events are not guaranteed to replay.
- Availability is VIN-, equipment-, market-, software-, and capability-dependent.

### High-value standard descriptors

| Descriptor | Value for this incident | Streaming |
|---|---|---|
| `vehicle.electronicControlUnit.diagnosticTroubleCodes.raw` | Raw fault memory; BMW describes potential errors and technical faults | Yes |
| `vehicle.status.checkControlMessages` | Last relevant warning transferred from vehicle; not every warning is transmitted | No |
| `vehicle.serviceDemand.ccm.notification` | Timestamp/event associated with Check Control notification | No |
| `vehicle.electricalSystem.battery.voltage` | Current low-voltage electrical-system battery voltage | Yes |
| `vehicle.electricalSystem.battery.stateOfCharge` | 12 V battery state of charge | Yes |
| `vehicle.electricalSystem.battery.stateOfChargePlausibility` | Plausibility of reported 12 V state of charge | No |
| `vehicle.electricalSystem.battery.serviceDemand.replace` | Battery condition/replacement demand | No |
| `vehicle.electricalSystem.battery.serviceDemand.recharge` | Battery recharge demand | No |
| `vehicle.channel.teleservice.lastBatteryGuardCallTime` | Last Battery Guard call timestamp | Yes |
| `vehicle.status.conditionBasedServices` | Condition Based Service data | No |
| `vehicle.status.conditionBasedServicesCount` | Count/report data; not all vehicle CBS messages transmit | Yes |

BMW's Telematics Data Catalogue marks raw fault memory and battery voltage as available across ICE, PHEV, and BEV and streamable, but actual VIN capability remains authoritative.[^3] Check Control and several service-demand values need REST snapshots rather than MQTT.

### Detailed seat-event package

BMW's public EU Data Act metadata currently defines `vehicle_euda_pack_vehicleusa_sem`. Relevant table `te_events_usagecounter_seat` includes:[^4]

- seat adjustment position and operated seat;
- adjustment command source, with observed range including `User Profile Position`;
- function state, including successful, hardware-error, canceled, and constraint-stop examples;
- activated constraint;
- event error ID, with examples such as `AntipinchMonitoringFailed` and `Wrong Assembly Seat Position`;
- module ID, affected function, adjustment command, function/mode, count, bus state, vehicle state, and UTC timestamp.

Related tables include `te_events_position_seat` and `te_events_seat_operation_dr`. This is unusually close to reported failure. However, metadata describes possible dataset columns and example/range values; it does **not** prove this vehicle produced those rows or that an owner archive exposes them. Confirm against downloaded archive and portal's VIN capability.

No standard CarData descriptor was found for stored memory preset, driver-profile-to-seat mapping, comfort-exit configuration, or actual longitudinal/height/backrest coordinates. That gap makes ECU diagnostics important.

## Programmatic historical and workshop routes

### Third-party Historic Data API

BMW's third-party Swagger exposes:

- `POST /exve/vehicles/{vin}/historicData`
- `GET /exve/vehicles/{vin}/historicData/{requestId}/status`
- `GET /exve/vehicles/{vin}/historicData/{requestId}`

Request schema accepts start/end timestamps, optional use cases, and an `allData` flag. Requests are asynchronous and require bearer credentials plus `X-clearance-id`, representing customer's explicit data clearance.[^9]

This is not available with current owner/customer client ID. It requires EEA company/service-provider registration, commercial setup, customer consent, and applicable fees. Building a company integration solely for one intermittent fault is poor first choice.

### Connected Repair and Maintenance Information (RMI)

Connected RMI offers strongest remote diagnostic model:

- service history: `/exve/services/serviceHistory/{assignmentUuid}`;
- repair history: `/exve/services/repairHistory/{assignmentUuid}`;
- diagnostic sessions and ECU/DTC readouts;
- DTC fields including `ACTIVE`, `PENDING`, or `PREVIOUSLY_ACTIVE`, occurrence counter, ECU ID, DTC timestamp, and title;
- DTC snapshots with parameters, descriptions, and fault properties.[^2]

Remote diagnostic access requires business/service setup and owner-approved vehicle assignment. BMW's current price list shows €3 net per repair-history request, €3 per service-history request, €3 per remote-info session, and €3 per remote-diagnostic session.[^10] A diagnostic session can be read/write and API includes clear-DTC and ECU-job operations. Any future integration should default to read-only; clearing faults or invoking ECU jobs needs separate explicit authorization.

## Existing repository fit

Current `/home/azureuser/bmw-cardata-imou` implementation:

- completes BMW Device Code + PKCE authorization;
- requests correct customer API and streaming scopes;
- refreshes rotating tokens and stores them with mode `0600`;
- consumes MQTT for GPS descriptors only;
- performs no CarData REST requests and stores no raw BMW telemetry history.

Therefore current service cannot answer what happened three weeks ago. Authentication foundation is reusable for customer REST and added MQTT descriptors, but historical reconstruction requires portal archive or B2B products.

Recommended implementation after archive review:

1. **Archive inspector:** read ZIP/CSV/JSON locally; inventory datasets, time ranges, VIN, and seat/fault/battery fields. No external writes.
2. **Future event recorder:** persist selected MQTT descriptors with original BMW timestamp, receipt timestamp, VIN, descriptor, value, and raw payload hash. Keep GPS and diagnostic retention separate.
3. **REST snapshot job:** once or twice daily fetch non-streamable CCM, CBS, and battery-service values, staying far below 50/day.
4. **Correlation report:** timeline seat errors, DTCs, 12 V state, profile-related adjustment commands, and service events.
5. **Alerts:** notify on new raw DTC, repeated seat function failure/constraint, low 12 V values, or battery-service demand.

Do not add remote fault clearing, ECU commands, or automatic dealer actions.

## Workshop request

Use this wording before another reset:

> Intermittent driver-seat memory, comfort-exit, and automatic BMW-profile positioning failed for about three weeks, then recovered. Please do not clear fault memory, reset modules, or program vehicle before exporting a complete ISTA vehicle test. Provide active, pending, and previously active DTCs, occurrence counters, timestamps, environment/snapshot data, and test plan for driver-seat controller, body-domain/profile communication, and 12 V energy management. Also provide current integration level, previous repair order, previous diagnostic report, and work performed when seat module was reset.

Ask specifically for:

- full vehicle-test PDF before and after work;
- model-specific driver-seat ECU name/address and DTC details;
- 12 V battery test, IBS/energy diagnosis, undervoltage history, and sleep-current findings;
- seat calibration/normalization status and mechanical/anti-trap constraints;
- BMW ID/profile association and key/profile reproduction test;
- software integration level before/after prior visit;
- repair and service history entries, defect codes, measure plan, and parts/programming records.

## Decision

Immediate sequence:

1. Request owner historic archive today.
2. Export archive and inspect seat/DTC/battery tables.
3. Book service only with explicit pre-reset ISTA export requirement.
4. Add read-only future telemetry collection after confirming VIN capability.
5. Consider Connected RMI or third-party historic API only if repeatable business use justifies setup and cost.

Success means one UTC timeline combining reported symptoms with seat adjustment source/state/errors, DTC status and snapshots, 12 V metrics, profile changes, and service actions. If archive contains none of those fields, result is still useful: it proves customer-level data boundary and shifts investigation to ECU/ISTA evidence.

## Sources

[^1]: BMW Romania, [EU Data Act information for BMW vehicles](https://www.bmw.ro/ro-ro/utilities/bmw/api/assets/cardata/CarDataFAQ_bmw_ro_ro.pdf). Covers volatile versus persistent ECU data, OBD access, typical deletion during service, backend transmission, and retention ranging from 30 days to product lifetime.
[^2]: BMW Group, [Connected RMI Services API specification](https://bmw-cardata.bmwgroup.com/thirdparty/public/assets/swagger/swagger-rmi-v2.json). Defines diagnostic sessions, DTC status/occurrence/timestamp/snapshot schemas, service history, repair history, clear-DTC jobs, and ECU jobs.
[^3]: BMW Group, [Telematics Data Catalogue](https://bmw-cardata.bmwgroup.com/thirdparty/api/public/v1/content/catalogue?language=en). Defines fault memory, Check Control, low-voltage battery, CBS, Teleservice, powertrain support, and streamability.
[^4]: BMW Group, [EU Data Act metadata download](https://bmw-cardata.bmwgroup.com/thirdparty/api/public/v1/content/metadata-file). Current package metadata defines seat usage, adjustment source/state, constraint, error, affected-function, and timestamp columns.
[^5]: BMW Group, [Connected Repair and Maintenance overview](https://bmw-cardata.bmwgroup.com/thirdparty/public/repair-and-maintenance/overview). Explains service setup, telematics prerequisites, customer permission, and fees.
[^6]: BMW Group, [BMW CarData overview](https://www.bmwgroup.com/en/general/regulations/cardata.html), and [Customer API Integration Guide](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation). Explain owner reports/archives, consent, backend storage, latest-data access, REST, streams, and no direct vehicle access.
[^7]: BMW Group, [Customer CarData API specification](https://bmw-cardata.bmwgroup.com/customer/public/assets/swagger/swagger-customer-api-v1.json). Current customer endpoints; no general historic-data route.
[^8]: BMW Group, [Customer API Integration Guide](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation). Documents primary-user prerequisites, OAuth, request/container limits, stream behavior, timestamps, and connection rules.
[^9]: BMW Group, [Third-party CarData API specification](https://bmw-cardata.bmwgroup.com/thirdparty/public/assets/swagger/swagger-cardata-v2.json). Defines date-bounded asynchronous Historic Data API and data-clearance header.
[^10]: BMW Group, [Connected RMI price list](https://bmw-cardata.bmwgroup.com/thirdparty/api/public/v1/content/download-content-file?language=en&applicationType=3PP&applicationCategory=RMI&contentCategory=Price_List&fileExtension=.md&version=API_v1). Prices valid from September 2025.
