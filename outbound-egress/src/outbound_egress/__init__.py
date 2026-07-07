"""Outbound Connector Engine (Project 5).

Consumes the plague Kinesis fan out stream, looks up which connectors a
tenant configured, applies an optional JQ transformation, and delivers the
resulting payload to an external system via webhook, cross-account Kinesis,
or a JWT OAuth protected endpoint.

By the time an event reaches this package it has already been ingested
(event-ingress), identity resolved (mci-core), and evaluated by the offer
state machine upstream. This package does not call mci-core.
"""
