pub const SYNORA_VERSION: &str = "0.1.0";

pub mod block;
pub mod chain;
pub mod execution;
pub mod hash;
pub mod mempool;
pub mod state;
pub mod transaction;

pub fn version() -> &'static str {
    SYNORA_VERSION
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synora_version_exists() {
        assert_eq!(version(), "0.1.0");
    }

    #[test]
    fn account_transfer_works() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];

        let mut state = state::State::new();

        state.create_account(alice, 1_000);
        state.create_account(bob, 0);

        state
            .transfer(alice, bob, 250)
            .expect("transfer should work");

        assert_eq!(state.get_account(&alice).unwrap().balance, 750);
        assert_eq!(state.get_account(&alice).unwrap().nonce, 1);
        assert_eq!(state.get_account(&bob).unwrap().balance, 250);
    }

    #[test]
    fn insufficient_balance_fails() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];

        let mut state = state::State::new();

        state.create_account(alice, 100);
        state.create_account(bob, 0);

        let result = state.transfer(alice, bob, 200);

        assert_eq!(result, Err("insufficient balance"));
    }

    #[test]
    fn transaction_cost_is_calculated() {
        let sender = [1u8; 20];
        let recipient = [2u8; 20];

        let tx = transaction::Transaction::new(1, 0, sender, recipient, 500, 21_000, 2, Vec::new());

        assert_eq!(tx.total_fee(), 42_000);
        assert_eq!(tx.total_cost(), 42_500);
        assert!(!tx.is_contract_call());
    }

    #[test]
    fn executor_can_execute_transaction() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = state::State::new();

        state.create_account(alice, 100_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let tx = transaction::Transaction::new(1, 0, alice, bob, 10_000, 21_000, 1, Vec::new());

        let executor = execution::Executor::new(1, fee_recipient);

        let receipt = executor
            .execute(&mut state, &tx)
            .expect("transaction should execute");

        assert!(receipt.success);
        assert_eq!(receipt.gas_used, 21_000);
        assert_eq!(receipt.fee_paid, 21_000);
        assert_eq!(receipt.value_transferred, 10_000);
        assert_eq!(receipt.transaction_hash, tx.hash());

        assert_eq!(state.get_account(&alice).unwrap().balance, 69_000);
        assert_eq!(state.get_account(&alice).unwrap().nonce, 1);
        assert_eq!(state.get_account(&bob).unwrap().balance, 10_000);
        assert_eq!(state.get_account(&fee_recipient).unwrap().balance, 21_000);
    }
}
