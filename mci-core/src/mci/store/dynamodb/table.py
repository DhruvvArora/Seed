"""DynamoDB access for the master-customer-index table.

Holds: key construction ({tenant_id}#{external_customer_id}), BatchGetItem
reads (up to 100 keys/call), and the conditional PutItem that creates a
mapping atomically using attribute_not_exists(partition_key). On a race
(ConditionalCheckFailedException) we re-read to get the winner's UUID.

TODO (next step): implement get_items, put_if_absent, delete_items.
"""
