part_number:
  pattern: "^[A-Z]{2,3}[0-9]{3}[PA]$"

naming:
  disallowed_tokens:
    - thing
    - item
    - part
    - unit
  disallowed_characters:
    - "_"
    - "-"
    - "."
  capitalization: title_case

bom_links:
  # strictly greater than zero: a 0-qty link is a data bug — remove the child instead
  quantity_min_exclusive: 0
  prevent_circular_references: true