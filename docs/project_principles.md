# Project Principles

## Purpose

Build a reliable, simple, and auditable sub routines for the bigger picture BOM / Inventory / Costing system that can start locally but remain compatible with future ERP/MRP migration.

## Principles

1. **Clarity over complexity**  
   Prefer simple, explicit structures over clever automation.

2. **Validate before import**  
   CSV data should be checked against rules

3. **Human-readable names, stable part numbers**  
   Names describe function. Part numbers provide traceability and system identity.

4. **One source of truth**  
   Core data lives in the csv; spreadsheets and UI views are inputs or outputs, not authorities.

5. **Manual review is part of the system**  
   Ambiguous, incomplete, or invalid records should be flagged for review, not silently fixed.

6. **Design for inheritance**  
   Someone else should be able to understand, edit, and extend the system without prior context.


10. **Build only what the MVP needs**  
   Prioritize ingestion, validation, review, editing, and useful outputs before advanced features.
