use crate::hash::Hash;
use crate::state::{Address, State};
use crate::transaction::Transaction;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionError {
    SenderNotFound,
    InsufficientBalance,
    TransferFailed,
    InvalidChainId,
    InvalidNonce,
    FeeRecipientNotFound,
    InvalidTransaction,
    InvalidSignature,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionReceipt {
    pub transaction_hash: Hash,
    pub success: bool,
    pub gas_used: u64,
    pub fee_paid: u128,
    pub value_transferred: u128,
}

pub struct Executor {
    chain_id: u64,
    fee_recipient: Address,
}

impl Executor {
    pub fn new(chain_id: u64, fee_recipient: Address) -> Self {
        Self {
            chain_id,
            fee_recipient,
        }
    }

    pub fn chain_id(&self) -> u64 {
        self.chain_id
    }

    pub fn fee_recipient(&self) -> Address {
        self.fee_recipient
    }

    pub fn execute(
        &self,
        state: &mut State,
        tx: &Transaction,
    ) -> Result<ExecutionReceipt, ExecutionError> {
        // ------------------------------------------------------------
        // 1. Chain ID harus sesuai.
        // ------------------------------------------------------------
        if tx.chain_id != self.chain_id {
            return Err(ExecutionError::InvalidChainId);
        }

        // ------------------------------------------------------------
        // 2. Validasi struktur transaksi.
        // ------------------------------------------------------------
        tx.validate()
            .map_err(|_| ExecutionError::InvalidTransaction)?;

        // ------------------------------------------------------------
        // 3. Signature wajib valid.
        //
        // Signature diverifikasi sebelum state disentuh.
        // ------------------------------------------------------------
        tx.verify_signature()
            .map_err(|_| ExecutionError::InvalidSignature)?;

        // ------------------------------------------------------------
        // 4. Sender harus ada.
        // ------------------------------------------------------------
        let sender = state
            .get_account(&tx.sender)
            .ok_or(ExecutionError::SenderNotFound)?;

        // ------------------------------------------------------------
        // 5. Nonce harus tepat.
        // ------------------------------------------------------------
        if sender.nonce != tx.nonce {
            return Err(ExecutionError::InvalidNonce);
        }

        // ------------------------------------------------------------
        // 6. Recipient harus ada.
        // ------------------------------------------------------------
        if state.get_account(&tx.recipient).is_none() {
            return Err(ExecutionError::TransferFailed);
        }

        // ------------------------------------------------------------
        // 7. Fee recipient harus ada.
        // ------------------------------------------------------------
        if state.get_account(&self.fee_recipient).is_none() {
            return Err(ExecutionError::FeeRecipientNotFound);
        }

        let value = u128::from(tx.value);

        let fee_u64 = tx
            .checked_total_fee()
            .ok_or(ExecutionError::InvalidTransaction)?;

        let fee = u128::from(fee_u64);

        let total_cost = value
            .checked_add(fee)
            .ok_or(ExecutionError::InsufficientBalance)?;

        // ------------------------------------------------------------
        // 8. Sender harus memiliki saldo yang cukup.
        // ------------------------------------------------------------
        if sender.balance < total_cost {
            return Err(ExecutionError::InsufficientBalance);
        }

        // ------------------------------------------------------------
        // 9. Execute secara atomic.
        //
        // Jika salah satu operasi gagal, state asli tidak berubah.
        // ------------------------------------------------------------
        let mut working_state = state.clone();

        working_state
            .transfer(tx.sender, tx.recipient, value)
            .map_err(|_| ExecutionError::TransferFailed)?;

        working_state
            .transfer_without_nonce(tx.sender, self.fee_recipient, fee)
            .map_err(|_| ExecutionError::TransferFailed)?;

        // ------------------------------------------------------------
        // 10. Commit hanya setelah semua operasi berhasil.
        // ------------------------------------------------------------
        *state = working_state;

        Ok(ExecutionReceipt {
            transaction_hash: tx.hash(),
            success: true,
            gas_used: tx.gas_limit,
            fee_paid: fee,
            value_transferred: value,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::Keypair;

    fn create_transaction(
        chain_id: u64,
        nonce: u64,
        keypair: &Keypair,
        recipient: Address,
        value: u64,
    ) -> Transaction {
        let mut tx = Transaction::new(
            chain_id,
            nonce,
            keypair.address(),
            recipient,
            value,
            21_000,
            1,
            Vec::new(),
        );

        tx.sign(keypair)
            .expect("test transaction should be signable");

        tx
    }

    fn create_executor(fee_recipient: Address) -> Executor {
        Executor::new(1, fee_recipient)
    }

    fn create_state(
        sender: Address,
        recipient: Address,
        fee_recipient: Address,
        sender_balance: u128,
    ) -> State {
        let mut state = State::new();

        state.create_account(sender, sender_balance);
        state.create_account(recipient, 0);
        state.create_account(fee_recipient, 0);

        state
    }

    #[test]
    fn executor_can_execute_transaction() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let tx = create_transaction(1, 0, &keypair, bob, 10_000);

        let executor = create_executor(fee_recipient);

        let receipt = executor
            .execute(&mut state, &tx)
            .expect("transaction should execute");

        assert!(receipt.success);
        assert_eq!(receipt.gas_used, 21_000);
        assert_eq!(receipt.fee_paid, 21_000);
        assert_eq!(receipt.value_transferred, 10_000);
        assert_eq!(receipt.transaction_hash, tx.hash());

        assert_eq!(
            state.get_account(&keypair.address()).unwrap().balance,
            69_000
        );
        assert_eq!(state.get_account(&keypair.address()).unwrap().nonce, 1);
        assert_eq!(state.get_account(&bob).unwrap().balance, 10_000);
        assert_eq!(state.get_account(&fee_recipient).unwrap().balance, 21_000);
    }

    #[test]
    fn second_transaction_uses_nonce_one() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 200_000);

        let executor = create_executor(fee_recipient);

        let tx1 = create_transaction(1, 0, &keypair, bob, 10_000);

        executor
            .execute(&mut state, &tx1)
            .expect("first transaction should execute");

        let tx2 = create_transaction(1, 1, &keypair, bob, 20_000);

        executor
            .execute(&mut state, &tx2)
            .expect("second transaction should execute");

        assert_eq!(state.get_account(&keypair.address()).unwrap().nonce, 2);
        assert_eq!(
            state.get_account(&keypair.address()).unwrap().balance,
            128_000
        );
        assert_eq!(state.get_account(&bob).unwrap().balance, 30_000);
        assert_eq!(state.get_account(&fee_recipient).unwrap().balance, 42_000);
    }

    #[test]
    fn insufficient_balance_does_not_modify_state() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100);

        let before = state.state_root();

        let tx = create_transaction(1, 0, &keypair, bob, 10_000);

        let executor = create_executor(fee_recipient);

        let result = executor.execute(&mut state, &tx);

        assert_eq!(result, Err(ExecutionError::InsufficientBalance));
        assert_eq!(state.state_root(), before);
    }

    #[test]
    fn wrong_chain_id_is_rejected() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let tx = create_transaction(2, 0, &keypair, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidChainId)
        );
    }

    #[test]
    fn wrong_nonce_is_rejected() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let tx = create_transaction(1, 1, &keypair, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidNonce)
        );
    }

    #[test]
    fn unsigned_transaction_is_rejected() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let tx = Transaction::new(1, 0, keypair.address(), bob, 10_000, 21_000, 1, Vec::new());

        let before = state.state_root();

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidSignature)
        );

        assert_eq!(state.state_root(), before);
    }

    #[test]
    fn invalid_signature_is_rejected() {
        let keypair = Keypair::generate();
        let other_keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let mut tx = create_transaction(1, 0, &keypair, bob, 10_000);

        let mut other_tx = create_transaction(1, 0, &other_keypair, bob, 10_000);

        tx.signature = other_tx.signature;

        let before = state.state_root();

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidSignature)
        );

        assert_eq!(state.state_root(), before);

        // Prevent an accidental optimization from making the test
        // misleading if signing internals change later.
        other_tx.signature = [0u8; 64];
    }

    #[test]
    fn modified_signed_transaction_is_rejected() {
        let keypair = Keypair::generate();

        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = create_state(keypair.address(), bob, fee_recipient, 100_000);

        let mut tx = create_transaction(1, 0, &keypair, bob, 10_000);

        tx.value += 1;

        let before = state.state_root();

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidSignature)
        );

        assert_eq!(state.state_root(), before);
    }
}
