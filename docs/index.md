# Guardian

---

> **WARNING: THIS SOFTWARE IS NOT READY FOR PRODUCTION OR REAL-WORLD USE!**
>
> Guardian is under active development. It is experimental, incomplete, and may contain serious bugs or security issues.
> **DO NOT use this software to protect children, enforce limits, or for any purpose where safety, privacy, or**
> **reliability matter.**
> Use at your own risk. The maintainers do NOT recommend or support real-world deployment at this time.

---

    Helping Families Build Healthy Media Habits

## What is Guardian?

Guardian is a parental control system for Linux that helps families guide their children toward a healthy relationship
with media, the internet, and especially video games. Guardian empowers parents to set reasonable boundaries, encourage
positive habits, and foster open conversations about digital life.

---

## Why Use Guardian?

- **Support healthy routines:** Set daily time limits and curfews for computer and gaming use, tailored to each child.
- **Encourage balance:** Help children learn to manage their screen time and prioritize school, sleep, and family activities.
- **Cross-device protection:** Guardian works across all family computers, laptops, and gaming devices, so limits are
  enforced everywhere.
- **Peace of mind:** Automated enforcement means parents don’t have to constantly monitor or argue about time spent online.
- **Transparency:** Children see friendly reminders and notifications, so expectations are clear and fair.
- **Parent dashboard:** Easily review usage, adjust limits, and grant bonus time when needed.

---

## How Guardian Works

Guardian is made up of several components that work together to provide robust, flexible, and family-friendly control:

### Device Service (guardian-daemon)

- Runs quietly in the background on each device.
- Tracks logins and actual usage time for each child.
- Enforces daily quotas and curfews, blocking logins or ending sessions when limits are reached.
- Works even if the device is offline; syncs with the central server when reconnected.

### Friendly Reminders (guardian-agent)

- Shows notifications to children as they approach their limits.
- Encourages self-regulation and positive habits.

### Parent Dashboard (guardian-hub - not yet implemented)

- Web-based and CLI tools for parents to view activity, adjust rules, and manage devices.
- See how much time each child has spent, across all devices.
- Grant bonus time or change limits instantly.

### Central Server (guardian-hub - not yet implemented)

- Keeps track of all devices, users, and policies.
- Ensures rules are consistent and up-to-date everywhere.
- Provides secure authentication and audit logs for accountability.

---

## Example: Setting Up Guardian for Your Family

1. **Create a Linux account for each child.**
2. **Install Guardian on all family devices.**
3. **Set daily time limits and curfew hours for each child in the dashboard.**
4. **Review usage reports and adjust rules as needed.**
5. **Talk with your children about healthy media habits and why these boundaries matter.**

---

## Frequently Asked Questions

**Does Guardian block specific games or websites?**

Not yet, but app allowlists and blocklists are planned for future releases. For now, Guardian focuses on overall time
and healthy routines.

**Can my child use their computer for homework after their gaming time is up?**

You can set different rules for weekdays, weekends, and bonus time. Guardian is flexible to fit your family’s needs.

**What happens if the internet goes down?**

Guardian enforces rules locally on each device, so limits and curfews still apply even if the server is offline.

**Is my family’s data safe?**

Guardian uses secure authentication and stores only what’s needed for enforcement. Parents control all settings and data.

---

## Tips for Parents

- Use Guardian as a tool for conversation, not just enforcement.
- Involve your children in setting limits and talk about why balance matters.
- Review usage together and celebrate positive habits.
- Adjust rules as your children grow and their needs change.

---

## Learn More & Get Started

Visit the [Guardian website](https://github.com/yourproject/guardian) for installation guides, troubleshooting, and
community support.

For technical details, see the [Developer Documentation](developer/index.md).
