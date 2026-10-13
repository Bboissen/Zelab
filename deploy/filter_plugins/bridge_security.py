"""Read-only provisioning checks for native Paperclip bridge contracts.

No credentials, requests or runtime policy live here. Return booleans so a
failed Ansible assertion never includes sensitive values in its explanation.
"""


def exact_scope(scope, project_id, assignees=()):
    if not isinstance(scope, dict) or scope.get('kind') != 'task_bridge':
        return False
    fields = {'kind', 'projectId', 'projectIds', 'parentIssueId', 'parentIssueIds',
              'allowedAssigneeAgentIds'}
    if set(scope) - fields:
        return False

    def ids(singular, plural):
        one, many = scope.get(singular), scope.get(plural)
        if one is not None and (not isinstance(one, str) or not one):
            raise ValueError
        if many is None:
            many = []
        if not isinstance(many, list) or any(not isinstance(x, str) or not x for x in many):
            raise ValueError
        return set(many) | ({one} if one is not None else set())

    try:
        allowed = scope.get('allowedAssigneeAgentIds')
        if allowed is None:
            allowed = []
        return (ids('projectId', 'projectIds') == {project_id}
                and not ids('parentIssueId', 'parentIssueIds')
                and isinstance(allowed, list)
                and all(isinstance(x, str) for x in allowed)
                and set(allowed) == set(assignees))
    except (TypeError, ValueError):
        return False


def bridge_identity(identity, company_id, agent_id, project_id, assignees=()):
    return (isinstance(identity, dict)
            and identity.get('id') == agent_id
            and identity.get('companyId') == company_id
            and exact_scope(identity.get('keyScope'), project_id, assignees))


def gateway_contract(agent, company_id, api_url, callback_url, secret_id):
    if not isinstance(agent, dict):
        return False
    config, permissions = agent.get('adapterConfig'), agent.get('permissions')
    if not isinstance(config, dict) or not isinstance(permissions, dict):
        return False
    key = config.get('apiKey')
    return (agent.get('companyId') == company_id
            and agent.get('adapterType') == 'hermes_gateway'
            and config.get('apiBaseUrl') == api_url
            and config.get('paperclipApiUrl') == callback_url
            and config.get('sessionKeyStrategy') == 'issue'
            and config.get('dangerouslyAllowInsecureRemoteHttp', False) is False
            and agent.get('defaultEnvironmentId') is None
            and isinstance(key, dict)
            and key.get('type') == 'secret_ref'
            and key.get('secretId') == secret_id
            and key.get('version') == 'latest'
            and permissions.get('canCreateAgents') is False
            and permissions.get('canCreateSkills') is False
            and permissions.get('canAssignTasks') is True
            and permissions.get('trustPreset') == 'standard')


def submission_contract(agent, company_id):
    if not isinstance(agent, dict):
        return False
    runtime, permissions = agent.get('runtimeConfig'), agent.get('permissions')
    if not isinstance(runtime, dict) or not isinstance(permissions, dict):
        return False
    heartbeat = runtime.get('heartbeat')
    return (isinstance(heartbeat, dict)
            and agent.get('companyId') == company_id
            and agent.get('adapterType') == 'process'
            and agent.get('adapterConfig') == {'command': '/usr/bin/false'}
            and agent.get('defaultEnvironmentId') is None
            and heartbeat.get('enabled') is False
            and heartbeat.get('wakeOnDemand') is False
            and permissions.get('canCreateAgents') is False
            and permissions.get('canCreateSkills') is False
            and permissions.get('canAssignTasks') is True
            and permissions.get('trustPreset') == 'standard')


class FilterModule:
    def filters(self):
        return {'zelab_exact_bridge_scope': exact_scope,
                'zelab_bridge_identity': bridge_identity,
                'zelab_gateway_contract': gateway_contract,
                'zelab_submission_contract': submission_contract}
