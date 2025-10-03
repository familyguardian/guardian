# Curfew Implementation Details

The curfew feature is implemented using the `pam_time.so` module, which enforces time-based access
restrictions for user accounts. The rules are defined in `/etc/security/time.conf`.

## Rule Generation

The `guardian-daemon` generates two main types of rules:

1. **Default Allow Rule**: A rule is created to explicitly allow all users who are **not** in the `kids`
   group to log in at any time. This is a critical safety measure to prevent administrators or other
   non-managed users from being locked out.

   ```plain
   *;*;!@kids;Al0000-2400
   ```

2. **Managed User Curfew Rules**: For each managed user, a specific rule is created that defines the
   allowed login times. This rule uses wildcards (`*`) for the service and TTY fields, meaning it
   applies to all forms of login for that user.

   ```plain
   *;*;kid1;Wk0800-2000&Sa0900-2200
   ```

## Important Note for Administrators

The use of a wildcard for the service field (`*`) means that the curfew is strictly enforced for the
managed user's account under all circumstances. This includes attempts by an administrator (like a
parent) to use `su` or `sudo` to switch to the user's account.

**Example:**

If `kid1` has a curfew and a parent tries to run `sudo -u kid1 bash` outside the allowed hours, the
action will be **denied** by PAM.

This is intentional behavior to ensure the curfew cannot be bypassed. If an administrator needs to
perform actions as the user during a restricted period, they will need to temporarily adjust the
user's policy to extend the curfew.
