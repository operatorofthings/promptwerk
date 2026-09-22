# Promptwerk

Eine öffentliche Werkstatt für bessere Arbeitsaufträge an KI-Agenten. Ohne Login,
mit bearbeitbarem Ergebnis, Erklärungen und gezielten Rückfragen. Alle Laufzeitdienste
liegen in AWS; keine externen Fonts, Analyse-Tracker oder LLM-APIs.

## Architektur

```text
Browser → CloudFront FREE + WAF
             ├─ private S3 assets (OAC)
             └─ IAM-protected Lambda URL (OAC) → DynamoDB quotas → Bedrock
                                                                Qwen3 32B
AWS Budgets / CloudWatch traffic alarm → SNS → Lambda concurrency = 0
```

Backend und Modell laufen in `eu-central-1` (Frankfurt). CloudFront ist ein globales
CDN. Der Bedrock-Aufruf verwendet ein mit `Project=promptwerk` markiertes
Application Inference Profile zur Zuordnung der Modellkosten.

## Kosten und Grenzen

- 200 Modellversuche pro Kalendermonat, 15 pro UTC-Tag global, 5 pro IP/UTC-Tag.
- DynamoDB reserviert **alle drei Kontingente atomar vor dem Modellaufruf**.
  Fehler, Zeitüberschreitungen und abgebrochene Browseranfragen werden nicht erstattet.
  Das AWS-SDK wiederholt Modellaufrufe nicht automatisch.
- Maximal 5.000 UTF-8-Bytes Nutzereingabe und 2.000 Ausgabetokens pro Versuch.
- Maximal zwei parallele Backend-Ausführungen. DynamoDB On-Demand-Durchsatz ist
  zusätzlich auf 5 Lese- und 5 Schreibeinheiten pro Sekunde begrenzt.
- WAF blockiert übermäßige Aufrufe pro IP; CloudWatch stoppt das Backend bei
  mindestens 100 Lambda-Aufrufen in fünf Minuten.
- Projektbudget: **6 USD pro Monat**, Warnungen bei tatsächlich mehr als 3, 5 und
  6 USD. Bei mehr als 6 USD deaktiviert SNS automatisch die Verarbeitung.
  Der zusätzliche Abstand zum Nutzerbudget von 10 EUR berücksichtigt Wechselkurs,
  Steuer und verzögert eintreffende Kosten. Steuern werden im Budget berücksichtigt,
  soweit AWS sie den markierten Ressourcen zuordnet.
- Die zugehörige CloudFront-Distribution und WAF laufen im **FREE-Flatrate-Plan**
  ohne CDN-Überziehungsgebühren. Es wird kein bezahlter Tarif automatisch gebucht.
- Normale Zielkosten: wenige USD monatlich bei dieser Last. Konservativer
  Modellkostenpuffer: 3 USD für 200 begrenzte Aufrufe. Dies ist eine Planungsreserve,
  keine bindende AWS-Preisgarantie; bei Modell-/Preisänderungen erneut prüfen.

**Kein AWS-Budget ist eine garantiert harte Rechnungsobergrenze.** Kostenberichte
und Budgetalarme können verzögert sein; neue Kostenzuordnungstags wirken nicht
rückwirkend und können bis zu 24 Stunden zur Verarbeitung benötigen. Kontingente,
abgesicherte Origins, WAF, Alarm und Abschaltung reduzieren das Risiko zusätzlich.
Ein Angriff kann die öffentliche Nutzung pausieren. Alarmabschaltungen werden
bewusst **nicht automatisch** rückgängig gemacht. Andere Anwendungen im Account
werden weder budgetiert noch abgeschaltet. Domainregistrierung ist separat und
wird von keinem Projektskript ausgeführt.

## Datenschutz

Promptwerk protokolliert weder Eingaben, Modellantworten noch vollständige
IP-Adressen. Der täglich wechselnde HMAC für IP-Limits wird mit geheimem Salt
gebildet; IPv6-Adressen werden pro /64 zusammengefasst. Zähler haben eine
TTL von 62 Tagen, anschließend löscht DynamoDB asynchron. Keine Anfrage-Samples
oder WAF-/CloudFront-Zugriffslogs sind aktiviert. Lambda-Systemlogs bleiben drei
Tage erhalten. Die Oberfläche weist auf die Verarbeitung durch AWS hin.

## Lokal testen

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
python3 -m http.server 8080 --directory web
```

Die lokale statische Vorschau führt keine Modellaufrufe aus. Die Live-API ist
bewusst nur hinter CloudFront erreichbar. `web/app.js` bildet für POST-Anfragen
den von Lambda-OAC benötigten SHA-256-Header.

## Deployment

Die bestehende AWS-CLI-Anmeldung muss für boto3 verfügbar sein. Benötigt werden
Rechte für CloudFormation, IAM, Bedrock, Lambda, DynamoDB, S3, CloudFront,
PricingPlanManager, WAF, Budgets, SNS, CloudWatch und Cost-Allocation-Tags.

```bash
.venv/bin/python infra/deploy.py --email deine-adresse@example.com --enable
```

`infra/template.py` ist die Quelle der CloudFormation-Definition;
`infra/template.json` wird daraus erzeugt. Das Deployment legt außerdem das
Inference Profile, WAF und den FREE-Plan über die AWS-API an und aktiviert den
Kosten-Tag. Die FREE-Plan-Verifizierung erfolgt **vor** dem ersten Einschalten
des Backends. Bei einer Störung bleibt das Backend gesperrt. Alle Hilfsressourcen
sind namensgebunden; das Skript sucht bestehende Ressourcen vor dem Anlegen.

`.deployment/` enthält lokale Outputs und Geheimnisse, ist nicht versioniert und
darf nicht veröffentlicht werden. Das Skript bewahrt eine bestehende
Alarmabschaltung, solange `--enable` nicht ausdrücklich gesetzt wird.

### Sofort abschalten / nach Prüfung wieder einschalten

```bash
aws lambda put-function-concurrency --region eu-central-1 \
  --function-name promptwerk-refine --reserved-concurrent-executions 0

# Erst Ursache / Kosten / Budgetalarm prüfen, dann bewusst freigeben:
aws lambda put-function-concurrency --region eu-central-1 \
  --function-name promptwerk-refine --reserved-concurrent-executions 2
```

### Eigene Domain später hinzufügen

Die Domain wird separat beschafft. Benötigt werden ein validiertes ACM-Zertifikat
in **us-east-1**, optional eine vorhandene Route-53-Zone, und der gewünschte Name.

```bash
.venv/bin/python infra/deploy.py --email deine-adresse@example.com \
  --domain promptwerk.example \
  --certificate arn:aws:acm:us-east-1:ACCOUNT:certificate/CERTIFICATE \
  --zone EXISTING_HOSTED_ZONE_ID
```

Bei externem DNS `--zone` weglassen und den Domainnamen auf die Distribution zeigen
lassen. Spätere Deployments müssen dieselben Domainargumente erhalten; leere
Argumente entfernen die Custom-Domain-Zuordnung. Die zugehörige Route-53-Zone kann
später dem bestehenden FREE-Plan zugeordnet werden, damit dessen DNS-Inklusivleistung
greift. Es werden keine Domains gekauft, übertragen oder verlängert.

### Aufräumen

Zuerst Lambda sperren, danach kontrolliert Distribution, FREE-Subscription,
CloudFormation-Stack, WAF und Inference Profile entfernen. S3 und DynamoDB sind
absichtlich mit `Retain` geschützt und bleiben bei Stack-Löschung erhalten;
bei endgültiger Stilllegung gezielt mit entfernen. Niemals fremde Account-Ressourcen
oder andere FREE-Subscriptions löschen.
