# Contributing to DayTrader Bot

First off, thank you for considering contributing to DayTrader Bot! It's people like you that make the open-source community such a powerful place to learn, inspire, and create.

## How to Contribute

### 1. Reporting Bugs
If you find a bug, please create an Issue on GitHub. Include:
- A clear descriptive title.
- The exact steps to reproduce the issue.
- Your OS, Python version, and a snippet of the error log (make sure to redact your Groww API tokens or personal data!).

### 2. Suggesting Enhancements
Have an idea for a new trading strategy? Want to improve the dashboard? 
- Open an Issue first to discuss it. We want to make sure it aligns with the project's goals before you spend hours coding it!

### 3. Submitting Pull Requests
1. **Fork** the repository and create your branch from `main`.
2. **Setup**: Run `pip install -r requirements.txt`.
3. **Code**: Add your feature or fix the bug. 
4. **Style**: Please ensure your code follows standard PEP-8 style guidelines.
5. **Test**: Run the bot in `paper` mode to ensure your changes don't break the main loop.
6. **Commit**: Write clear, concise commit messages.
7. **Submit**: Open a Pull Request!

## Adding a New Trading Strategy

The bot is designed to be extensible. If you want to add a new strategy:
1. Create a new file in `src/strategies/`.
2. Inherit from `BaseStrategy`.
3. Implement `calculate_indicators()` and `generate_signal()`.
4. Add it to the `StrategyEnsemble` list in `src/main.py`.
5. Add the strategy parameters to `config.yaml`.

## Important Rules
- **No real money tests in PRs**: Never submit a PR that forces live mode. Default configurations must ALWAYS remain in `paper` mode to protect users.
- **Keep it headless**: The core bot logic should never require a GUI or user input to run.

Thank you for contributing!
